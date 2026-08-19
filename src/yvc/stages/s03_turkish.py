"""Turkish correctness validation and measurement.

Diacritic accuracy is a graded output, so this stage produces numbers
rather than assurances. Three checks need no reference data and run on
every transcript; a fourth compares against YouTube's own Turkish
auto-captions, which cost nothing to fetch and give an independent
signal.

What this stage deliberately does *not* do is aggressively "fix" text.
Ambiguous restorations (`kar`/`kâr`, `acı`/`açı`, `sac`/`saç`) are left
alone. Guessing wrong is worse than leaving a word as the model heard it,
because a wrong correction is invisible downstream while a missing one is
merely a transcription error.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from yvc.bootstrap import child_env
from yvc.io import read_json, write_json
from yvc.turkish.casing import (
    DIACRITICS,
    ascii_fold,
    diacritic_density,
    has_forbidden_combining,
    nfc,
    tr_lower,
)

# Domain words that must survive with diacritics intact. If these appear
# ASCII-folded, the transcript has a systematic problem rather than a
# scattering of individual errors.
# Every entry must actually contain a Turkish diacritic; a word like
# "vergi" is all-ASCII and cannot demonstrate anything about folding.
SENTINEL_WORDS = [
    "şirket", "çalışan", "ücret", "maaş", "işveren", "kıdem",
    "yüzde", "değil", "böyle", "günü", "için", "gün",
    "büyük", "küçük", "öğrenci", "görüş", "düşün", "çünkü", "işçi",
]


@dataclass
class QualityReport:
    diacritic_density: float
    density_verdict: str
    combining_marks_found: bool
    sentinel_hits: dict
    suspicious_ascii: list
    youtube_agreement: float | None
    word_count: int
    char_count: int


def fetch_youtube_captions(video_id: str, out_dir: Path, yt_dlp: str) -> str | None:
    """Download YouTube's Turkish auto-captions as a plain-text reference.

    Free, independent, and good enough for an agreement ratio even though
    auto-captions are themselves imperfect. It is a cross-check, not a
    gold standard, and is reported as such.
    """
    try:
        subprocess.run(
            [
                yt_dlp, "--skip-download", "--write-auto-subs",
                "--sub-langs", "tr", "--sub-format", "vtt",
                "--output", str(out_dir / "yt_ref.%(ext)s"),
                f"https://www.youtube.com/watch?v={video_id}",
            ],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=child_env(), timeout=300,
        )
    except Exception:
        return None

    for candidate in out_dir.glob("yt_ref*.vtt"):
        raw = candidate.read_text(encoding="utf-8", errors="replace")
        lines = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:")):
                continue
            lines.append(re.sub(r"<[^>]+>", "", line))
        return nfc(" ".join(lines))
    return None


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text, flags=re.UNICODE)


def analyze(
    transcript_path: str | Path,
    out_path: str | Path,
    *,
    video_id: str | None = None,
    yt_dlp: str | None = None,
    min_density: float = 35.0,
) -> dict:
    """Validate a transcript and write quality_report.json."""
    data = read_json(transcript_path)
    text = nfc(" ".join(seg["text"] for seg in data["segments"]))
    tokens = _tokens(text)

    density = diacritic_density(text)
    if density < min_density:
        verdict = "FAIL_SYSTEMATIC_ASCII_FOLDING"
    elif density < 55:
        verdict = "LOW"
    else:
        verdict = "OK"

    combining = has_forbidden_combining(text)

    # Sentinel check: for each domain word, how often does the correct
    # diacritic form appear versus its ASCII shadow?
    lowered = [tr_lower(t) for t in tokens]
    counts = Counter(lowered)
    sentinel_hits = {}
    for word in SENTINEL_WORDS:
        folded = ascii_fold(word)
        if folded == word:
            # The word carries no diacritics, so its "ASCII-folded" form is
            # itself. Counting both would double-count the same occurrences
            # and report a fake 50% failure rate.
            continue
        good = counts.get(word, 0)
        bad = counts.get(folded, 0)
        if good or bad:
            sentinel_hits[word] = {
                "correct": good,
                "ascii_folded": bad,
                "ratio": round(good / (good + bad), 3) if (good + bad) else None,
            }

    # Words that are pure ASCII yet contain letters that in Turkish are
    # usually accented. Reported for review, never auto-corrected.
    at_risk = set("cgiosu")
    suspicious = []
    for word, freq in counts.most_common():
        if len(word) < 4 or freq < 3:
            continue
        if any(ch in DIACRITICS for ch in word):
            continue
        if not word.isascii() or not word.isalpha():
            continue
        if sum(1 for ch in word if ch in at_risk) >= 2:
            suspicious.append({"word": word, "count": freq})
        if len(suspicious) >= 40:
            break

    agreement = None
    if video_id and yt_dlp:
        reference = fetch_youtube_captions(video_id, Path(out_path).parent, yt_dlp)
        if reference:
            agreement = _agreement(text, reference)

    report = QualityReport(
        diacritic_density=round(density, 2),
        density_verdict=verdict,
        combining_marks_found=combining,
        sentinel_hits=sentinel_hits,
        suspicious_ascii=suspicious,
        youtube_agreement=agreement,
        word_count=len(tokens),
        char_count=len(text),
    )

    payload = {
        "model": data.get("model", {}),
        "metrics": report.__dict__,
        "thresholds": {
            "min_diacritic_density": min_density,
            "expected_range": "60-90 per 1000 chars for running Turkish text",
        },
        "notes": [
            "Ambiguous diacritic restorations are intentionally not applied.",
            "youtube_agreement compares against auto-captions, which are "
            "themselves imperfect; treat it as a cross-check, not ground truth.",
        ],
    }
    write_json(out_path, payload)

    print(f"[turkish] diacritic density {density:.1f}/1000 -> {verdict}")
    print(f"[turkish] combining marks present: {combining}")
    if agreement is not None:
        print(f"[turkish] YouTube caption agreement: {agreement:.1%}")
    folded = [w for w, v in sentinel_hits.items() if v["ascii_folded"] > 0]
    if folded:
        print(f"[turkish] WARNING sentinel words seen ASCII-folded: {folded}")
    print(f"[turkish] -> {out_path}")
    return payload


def _agreement(candidate: str, reference: str) -> float:
    """Bag-of-words overlap after ASCII folding and lowercasing.

    Folding both sides first means this measures whether the same *words*
    were heard, independent of diacritics -- so a low score indicates a
    genuine recognition difference rather than a formatting one.
    """
    a = Counter(ascii_fold(tr_lower(w)) for w in _tokens(candidate))
    b = Counter(ascii_fold(tr_lower(w)) for w in _tokens(reference))
    if not a or not b:
        return 0.0
    shared = sum((a & b).values())
    return round(2 * shared / (sum(a.values()) + sum(b.values())), 4)


if __name__ == "__main__":
    import sys

    import yvc.bootstrap  # noqa: F401

    base = Path(sys.argv[1] if len(sys.argv) > 1 else "work/r39OrneyMDs")
    analyze(
        base / "transcript.json",
        base / "quality_report.json",
        video_id=base.name,
        yt_dlp=sys.argv[2] if len(sys.argv) > 2 else "tools/bin/yt-dlp.exe",
    )
