"""Per-platform social copy, grounded in the clip's own transcript.

Generic filler is not discouraged by instruction -- it is made
structurally impossible. The output schema requires an
``evidence_quote`` that must appear verbatim in the clip transcript, and
a ``key_number`` that must appear there too whenever the clip contains
digits. A post reading "Bu videoda maaşlar konuşuluyor, kaçırmayın"
cannot produce a passing evidence quote, so it fails validation and is
regenerated.

One LLM call per clip returns every platform at once: five calls per
video instead of twenty-five, which matters because each CLI invocation
carries several seconds of latency and a fixed token cost.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel, Field

from yvc.io import read_json, write_json
from yvc.llm.claude_cli import ClaudeCLI, LLMError
from yvc.llm.guard import require_success_ratio
from yvc.llm.pool import concurrency_of, map_ordered

def _is_failed_group(group: list[dict]) -> bool:
    """A clip whose copy never arrived is written as a single failed row."""
    return len(group) == 1 and group[0].get("status") == "failed"


PLATFORM_SPECS = {
    "linkedin": {
        "max": 3000, "target": (700, 1300), "hashtags": (3, 5),
        "tone": "Profesyonel, veri odaklı, birinci tekil uzman sesi",
        "layout": "İlk 140 karakter 'daha fazla' kesmesinden önce görünür: "
                  "tek satır kanca, boş satır, 2-3 cümlelik paragraflar",
        "cta": "Soru sorarak tartışma başlat",
    },
    "instagram": {
        "max": 2200, "target": (125, 300), "hashtags": (8, 12),
        "tone": "Samimi ama uzman, en fazla 3 emoji",
        "layout": "Tek satır kanca, 2-3 kısa satır, sonda hashtag bloğu",
        "cta": "Kaydet / profildeki link",
    },
    "x": {
        "max": 280, "target": (200, 265), "hashtags": (1, 2),
        "tone": "Keskin, iddialı, rakam önde",
        "layout": "Tek tweet; link 23 karakter sayılır",
        "cta": "Link ile bitir",
    },
    "tiktok": {
        "max": 2200, "target": (80, 150), "hashtags": (3, 5),
        "tone": "Çok konuşma dili, soruyla açılış",
        "layout": "Tek blok",
        "cta": "Yorumla / kaydet",
    },
    "youtube": {
        "max": 5000, "target": (200, 400), "hashtags": (3, 4),
        "tone": "Arama odaklı, anahtar kelime başta",
        "layout": "Başlık bağımsız okunur; açıklamanın ilk satırı da öyle",
        "cta": "Abone ol + link",
    },
}

BANNED_DEFAULT = [
    "bu videoda", "kaçırmayın", "mutlaka izleyin", "inanılmaz",
    "çok önemli bir konu", "herkesin bilmesi gereken", "game changer",
]


class PlatformCopy(BaseModel):
    body: str
    hashtags: list[str] = Field(default_factory=list)
    cta: str = ""
    title: str | None = None


class ClipCopy(BaseModel):
    linkedin: PlatformCopy
    instagram: PlatformCopy
    x: PlatformCopy
    tiktok: PlatformCopy
    youtube: PlatformCopy
    evidence_quote: str
    key_number: str | None = None
    angle: str
    body_en: str | None = None


PROMPT = """Aşağıdaki klip, Türkçe bir panelden alınmış. Her platform için
KLİBİN KENDİ İÇERİĞİNDEN üretilmiş, birbirinden farklı sosyal medya metni yaz.

KLİP METNİ:
\"\"\"{text}\"\"\"

KANCA TİPİ: {hook_type}
KANCA CÜMLESİ: {hook_line}
MARKA: {brand} — {persona}
HEDEF KİTLE: {audience}
LİNK: {link}

ZORUNLU KANIT ALANLARI:
- evidence_quote: Yukarıdaki klip metninden BİREBİR kopyalanmış, en az 6 kelimelik
  bir alıntı. Kelimesi kelimesine aynı olmalı; özetleme, yeniden yazma.
- key_number: Klipte geçen bir rakam/oran (varsa). Klipte geçmeyen rakam UYDURMA.
- angle: rakam | çelişki | soru | hikaye | uygulama

PLATFORM KURALLARI:
{specs}

KURALLAR:
- Her platform metni GERÇEKTEN FARKLI olsun; aynı metni kopyalayıp yapıştırma.
- Şu ifadeleri ASLA kullanma: {banned}
- Klipte söylenmeyen hiçbir iddiada bulunma.
- youtube.title alanını doldur (en fazla 90 karakter).
- body_en: LinkedIn metninin İngilizce transcreation'ı (çeviri değil, uyarlama).
"""



# Whisper writes numbers as words at least as often as digits in Turkish
# speech ("yuzde kirki", not "%40"). Checking only for the digit form
# would reject correctly-grounded copy, so both are accepted.
TR_NUMBER_WORDS = {
    "sifir": 0, "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5,
    "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9, "on": 10,
    "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50, "altmis": 60,
    "yetmis": 70, "seksen": 80, "doksan": 90, "yuz": 100, "bin": 1000,
    "milyon": 1000000, "milyar": 1000000000,
}


def _spelled_numbers(text: str) -> set[int]:
    """Numbers mentioned as words, including simple compounds (kirk bes)."""
    from yvc.turkish.casing import ascii_fold, tr_lower

    tokens = re.findall(r"\w+", ascii_fold(tr_lower(text)), flags=re.UNICODE)
    found: set[int] = set()
    run = 0
    for token in tokens:
        value = TR_NUMBER_WORDS.get(token)
        if value is None:
            # Turkish is agglutinative: "kirki", "kirkin", "binden" are all
            # the number word plus a case suffix. Match on the stem, but
            # bound the suffix length so "bir" does not swallow "birlikte".
            for stem, stem_value in TR_NUMBER_WORDS.items():
                if (
                    len(token) > len(stem)
                    and token.startswith(stem)
                    and len(token) - len(stem) <= 3
                ):
                    value = stem_value
                    break
        if value is None:
            if run:
                found.add(run)
                run = 0
            continue
        if value >= 100 and run:
            run *= value
        else:
            run += value
        found.add(value)
    if run:
        found.add(run)
    return found


def _norm(text: str) -> str:
    """NFC + whitespace collapse, so quote matching is not defeated by
    formatting differences the model introduces."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().lower()


def validate_copy(
    copy: ClipCopy, clip_text: str, banned: list[str]
) -> list[dict]:
    """Programmatic gates. Errors force regeneration; warnings are recorded."""
    issues: list[dict] = []
    haystack = _norm(clip_text)

    # The anti-filler gate.
    quote = _norm(copy.evidence_quote)
    if len(quote.split()) < 6:
        issues.append({"code": "EVIDENCE_TOO_SHORT", "severity": "error",
                       "detail": f"{len(quote.split())} words, need >= 6"})
    elif quote not in haystack:
        issues.append({"code": "EVIDENCE_NOT_IN_TRANSCRIPT", "severity": "error",
                       "detail": f"quote not found verbatim: {copy.evidence_quote[:60]!r}"})

    # Numbers must come from the clip, not the model's imagination.
    if copy.key_number:
        digits = re.sub(r"[^\d]", "", copy.key_number)
        if digits:
            in_digits = digits in re.sub(r"[^\d]", "", clip_text)
            in_words = int(digits) in _spelled_numbers(clip_text)
            if not in_digits and not in_words:
                issues.append({"code": "NUMBER_HALLUCINATION", "severity": "error",
                               "detail": f"key_number {copy.key_number!r} appears in the "
                                         "clip neither as digits nor as Turkish words"})

    bodies: dict[str, str] = {}
    for platform, spec in PLATFORM_SPECS.items():
        block: PlatformCopy = getattr(copy, platform)
        bodies[platform] = block.body

        length = len(block.body)
        if platform == "x" and "http" in block.body:
            length = len(re.sub(r"https?://\S+", "x" * 23, block.body))
        if length > spec["max"]:
            issues.append({"code": "LEN_OVER", "severity": "error",
                           "detail": f"{platform}: {length} > {spec['max']}"})

        low, high = spec["hashtags"]
        if not (low <= len(block.hashtags) <= high):
            issues.append({"code": "HASHTAG_COUNT", "severity": "warn",
                           "detail": f"{platform}: {len(block.hashtags)} not in {low}-{high}"})

        lowered = block.body.lower()
        for phrase in banned:
            if phrase.lower() in lowered:
                issues.append({"code": "BANNED_PHRASE", "severity": "error",
                               "detail": f"{platform}: {phrase!r}"})

    # Near-identical text across platforms defeats the point of writing
    # five versions. Surfaced as a warning so a reviewer can see it.
    names = list(bodies)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if _jaccard(bodies[names[i]], bodies[names[j]]) > 0.85:
                issues.append({
                    "code": "CROSS_PLATFORM_DUPLICATE", "severity": "warn",
                    "detail": f"{names[i]} and {names[j]} are near-identical",
                })
    return issues


def _jaccard(a: str, b: str, n: int = 3) -> float:
    def grams(text: str) -> set:
        words = _norm(text).split()
        return {tuple(words[i : i + n]) for i in range(max(0, len(words) - n + 1))}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def write_copy(
    clips_path: str | Path,
    out_path: str | Path,
    *,
    brand_path: str | Path = "config/brand.json",
    routing_by_aspect: dict | None = None,
    llm: ClaudeCLI | None = None,
    model: str | None = "sonnet",
    min_success_ratio: float = 0.6,
) -> dict:
    brand = read_json(brand_path)
    clips = read_json(clips_path)["clips"]
    routing = routing_by_aspect or {}
    llm = llm or ClaudeCLI()
    banned = brand.get("banned_phrases", BANNED_DEFAULT)

    specs_text = "\n".join(
        f"- {name}: max {s['max']} karakter, hedef {s['target'][0]}-{s['target'][1]}, "
        f"{s['hashtags'][0]}-{s['hashtags'][1]} hashtag. Ton: {s['tone']}. "
        f"Düzen: {s['layout']}. CTA: {s['cta']}"
        for name, s in PLATFORM_SPECS.items()
    )

    def _copy_one(index: int, clip: dict) -> list[dict]:
        """Write one clip's copy. Returns that clip's post rows.

        The two-attempt repair below stays sequential *within* a clip --
        the second attempt is built from the first one's errors -- while
        different clips overlap.
        """
        clip_posts: list[dict] = []
        link = (
            f"{brand['destination_url']}?utm_source={{platform}}&utm_medium=social_organic"
            f"&utm_campaign=datassist_clips&utm_content={clip['clip_id']}"
            f"&utm_term={clip.get('hook_type', '')}"
        )
        prompt = PROMPT.format(
            text=clip["text"][:3500],
            hook_type=clip.get("hook_type", ""),
            hook_line=clip.get("hook_line", ""),
            brand=brand["name"],
            persona=brand["voice"]["persona"],
            audience=brand["voice"]["audience"],
            link=link,
            specs=specs_text,
            banned=", ".join(banned),
        )

        issues: list[dict] = []
        copy_obj = None
        for attempt in (1, 2):
            try:
                result = llm.complete(
                    f"copy.{clip['clip_id']}.a{attempt}", prompt, ClipCopy, model=model
                )
            except LLMError as exc:
                issues = [{"code": "LLM_FAILED", "severity": "error", "detail": str(exc)[:200]}]
                break

            copy_obj = result.data
            issues = validate_copy(copy_obj, clip["text"], banned)
            errors = [i for i in issues if i["severity"] == "error"]
            if not errors:
                break
            # Feed the failures back once before giving up on this clip.
            prompt += (
                "\n\nÖNCEKİ DENEME ŞU HATALARLA REDDEDİLDİ:\n"
                + "\n".join(f"- {i['code']}: {i['detail']}" for i in errors)
                + "\nBunları düzelt. evidence_quote klip metninden BİREBİR olmalı."
            )

        if copy_obj is None:
            print(f"[copy] {clip['clip_id']} FAILED")
            return [{"clip_id": clip["clip_id"], "status": "failed", "issues": issues}]

        targets = routing.get(clip["aspect"], list(PLATFORM_SPECS)) if routing else list(PLATFORM_SPECS)
        for platform in targets:
            block: PlatformCopy = getattr(copy_obj, platform)
            clip_posts.append({
                "post_id": f"{clip['clip_id']}-{platform}-A",
                "clip_id": clip["clip_id"],
                "variant": "A",
                "platform": platform,
                "text": block.body,
                "hashtags": block.hashtags,
                "cta": block.cta,
                "title": block.title,
                "text_en": copy_obj.body_en if platform in ("linkedin", "x") else None,
                "evidence_quote": copy_obj.evidence_quote,
                "key_number": copy_obj.key_number,
                "angle": copy_obj.angle,
                "hook_type": clip.get("hook_type"),
                "tracking_url": link.replace("{platform}", platform),
                "char_count": len(block.body),
                "validation": {
                    "passed": not [i for i in issues if i["severity"] == "error"],
                    "issues": issues,
                },
                "status": "ok",
            })

        errs = len([i for i in issues if i["severity"] == "error"])
        print(
            f"[copy] {clip['clip_id']}: 5 platforms, "
            f"{errs} errors, {len(issues) - errs} warnings"
        )
        return clip_posts

    # One call per clip, and clips do not reference each other.
    groups = map_ordered(_copy_one, clips, concurrency_of(llm))
    require_success_ratio(
        "copywrite",
        sum(1 for g in groups if not _is_failed_group(g)),
        len(groups),
        min_success_ratio,
    )
    posts: list[dict] = [post for group in groups for post in group]

    payload = {"posts": posts, "platform_specs": PLATFORM_SPECS}
    write_json(out_path, payload)
    print(f"[copy] {len(posts)} posts -> {out_path}")
    return payload


if __name__ == "__main__":
    import sys

    import yvc.bootstrap  # noqa: F401

    base = Path(sys.argv[1] if len(sys.argv) > 1 else "work/r39OrneyMDs")
    write_copy(base / "clips.json", base / "posts.json")
