"""Publish-time selection with an auditable rationale.

The brief requires being able to explain *why* a time was chosen, so the
rationale is emitted as structured data rather than a sentence assembled
after the fact. Rules live in a table with ids; the planner records which
rules fired, what they scored, and which slots lost.

The timing defaults are reasoned, not measured -- they encode how a
Turkish B2B/HR audience uses each platform. Rule D1 exists precisely
because they are assumptions: once the hook database holds 20 or more
observations for a platform, observed performance starts overriding the
default, weighted by sample size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Iterable

TR = timezone(timedelta(hours=3))  # Europe/Istanbul, no DST since 2016

# id -> (platform, weekdays, window, base weight, rationale)
# weekdays: 0=Mon .. 6=Sun
RULES: list[dict] = [
    {
        "id": "S1", "platform": "linkedin", "days": [1, 2, 3],
        "window": (time(8, 0), time(9, 15)), "weight": 1.0,
        "rationale_tr": "TR beyaz yakalı mesai 09:00'da başlıyor; işe geliş ve "
                        "ilk kahve arasında feed taraması en yoğun.",
    },
    {
        "id": "S2", "platform": "linkedin", "days": [1, 2, 3],
        "window": (time(12, 30), time(13, 30)), "weight": 0.8,
        "rationale_tr": "Öğle arası ikinci pik; sabah penceresini kaçıran "
                        "karar vericiler burada yakalanıyor.",
    },
    {
        "id": "S3", "platform": "instagram", "days": [0, 1, 2, 3, 4, 5, 6],
        "window": (time(20, 0), time(22, 30)), "weight": 1.0,
        "rationale_tr": "Reels dağıtımı ilk 60-90 dakikadaki etkileşime duyarlı; "
                        "akşam prime time'da hedef kitle uyanık ve boşta.",
    },
    {
        "id": "S4", "platform": "instagram", "days": [0, 1, 2, 3, 4],
        "window": (time(12, 0), time(13, 30)), "weight": 0.75,
        "rationale_tr": "Öğle molası ikincil pik.",
    },
    {
        "id": "S5", "platform": "x", "days": [0, 1, 2, 3, 4],
        "window": (time(9, 0), time(10, 0)), "weight": 1.0,
        "rationale_tr": "Haber ve görüş tüketimi sabah saatlerinde yoğun.",
    },
    {
        "id": "S6", "platform": "x", "days": [0, 1, 2, 3, 4],
        "window": (time(21, 0), time(22, 30)), "weight": 0.8,
        "rationale_tr": "TR'de akşam ikinci tartışma piki.",
    },
    {
        "id": "S7", "platform": "tiktok", "days": [2, 3, 4, 5, 6],
        "window": (time(19, 0), time(22, 30)), "weight": 1.0,
        "rationale_tr": "Eğlence tüketimi akşam ağırlıklı; B2B içerik burada "
                        "genel izleyiciye dağıtılıyor.",
    },
    {
        "id": "S8", "platform": "youtube", "days": [0, 1, 2, 3, 4, 5, 6],
        "window": (time(18, 0), time(21, 0)), "weight": 1.0,
        "rationale_tr": "Shorts shelf tüketimi akşam yoğunlaşıyor.",
    },
]

# Content-type multipliers: which hook types suit which part of the day.
CONTENT_FACTORS = {
    "data_number": {"morning": 0.15, "evening": 0.0,
                    "why": "Karar-destek içeriği iş saatinde daha çok kaydediliyor."},
    "contrarian": {"morning": 0.0, "evening": 0.15,
                   "why": "Tartışmalı içerik boş zamanda daha çok yorum topluyor."},
    "howto": {"morning": 0.05, "evening": 0.05,
              "why": "Uygulamalı içerik mola saatlerinde tüketiliyor."},
    "story": {"morning": 0.0, "evening": 0.10,
              "why": "Anlatı içeriği akşam daha uzun izleniyor."},
    "question": {"morning": 0.10, "evening": 0.05,
                 "why": "Soru formatı sabah etkileşimi tetikliyor."},
}

MIN_GAP_SAME_PLATFORM_H = 4
CROSS_PLATFORM_STAGGER_MIN = 30

# Fixed-date Turkish public holidays. Religious holidays move yearly and
# would need a lunar calendar; they are out of scope and noted as such.
TR_FIXED_HOLIDAYS = {(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)}


@dataclass
class ScheduleRationale:
    rule_ids: list[str]
    audience_tz: str
    audience_segment: str
    platform_pattern: str
    content_factor: str
    weekday_choice: str
    window_local: str
    chosen_local: str
    chosen_utc: str
    score: float
    competing_slots: list[dict] = field(default_factory=list)
    constraints_applied: list[str] = field(default_factory=list)
    source: str = "rule_default"


def _part_of_day(t: time) -> str:
    return "morning" if t.hour < 15 else "evening"


def _jitter_minutes(seed: str) -> int:
    """Deterministic +/-12 minute offset.

    A fixed publish minute across dozens of posts is a visible automation
    fingerprint. Deriving it from the post id keeps runs reproducible
    while breaking the pattern.
    """
    import hashlib

    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return (int(digest[:4], 16) % 25) - 12


def plan(
    post_id: str,
    platform: str,
    hook_type: str,
    *,
    now: datetime,
    already_planned: Iterable[datetime] = (),
    clip_siblings: Iterable[datetime] = (),
    observed: dict | None = None,
    horizon_days: int = 7,
) -> tuple[datetime, ScheduleRationale]:
    """Choose a publish time and explain it.

    ``observed`` optionally carries {slot_key: {"lift": float, "n": int}}
    from the hook database. Its influence grows as n/(n+20), so a handful
    of data points nudge the default rather than replacing it.
    """
    candidates: list[dict] = []
    rules = [r for r in RULES if r["platform"] == platform] or [
        {
            "id": "S0", "platform": platform, "days": [1, 2, 3],
            "window": (time(10, 0), time(11, 0)), "weight": 0.5,
            "rationale_tr": "Platform için özel kural yok; iş saati varsayılanı.",
        }
    ]

    for day_offset in range(horizon_days):
        day = (now + timedelta(days=day_offset)).astimezone(TR)
        for rule in rules:
            if day.weekday() not in rule["days"]:
                continue

            start, _ = rule["window"]
            slot = day.replace(
                hour=start.hour, minute=start.minute, second=0, microsecond=0
            )
            if slot <= now.astimezone(TR) + timedelta(minutes=30):
                continue

            score = rule["weight"]
            factors: list[str] = []

            factor = CONTENT_FACTORS.get(hook_type)
            if factor:
                bonus = factor[_part_of_day(start)]
                if bonus:
                    score += bonus
                    factors.append(factor["why"])

            source = "rule_default"
            key = f"{platform}:{day.weekday()}:{start.hour}"
            if observed and key in observed:
                stats = observed[key]
                n = stats.get("n", 0)
                shrink = n / (n + 20)
                score += stats.get("lift", 0.0) * shrink
                if n >= 20:
                    source = "rule+db_blend"
                factors.append(f"gözlem n={n}, ağırlık {shrink:.2f}")

            score -= day_offset * 0.02  # prefer sooner, all else equal

            candidates.append({
                "rule": rule, "slot": slot, "score": round(score, 4),
                "factors": factors, "source": source, "day_offset": day_offset,
            })

    if not candidates:
        fallback = (now.astimezone(TR) + timedelta(hours=24)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        return fallback, ScheduleRationale(
            rule_ids=["FALLBACK"], audience_tz="Europe/Istanbul",
            audience_segment="TR B2B / İK", platform_pattern="none",
            content_factor="", weekday_choice="", window_local="09:00",
            chosen_local=fallback.isoformat(),
            chosen_utc=fallback.astimezone(timezone.utc).isoformat(),
            score=0.0, constraints_applied=["no_rule_matched"],
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)

    planned = sorted(d.astimezone(TR) for d in already_planned)
    siblings = sorted(d.astimezone(TR) for d in clip_siblings)
    constraints: list[str] = []

    chosen = None
    for candidate in candidates:
        slot = candidate["slot"]
        if any(abs((slot - p).total_seconds()) < MIN_GAP_SAME_PLATFORM_H * 3600
               for p in planned):
            continue  # G1: self-cannibalisation
        if (slot.month, slot.day) in TR_FIXED_HOLIDAYS:
            constraints.append("G3_holiday_skipped")
            continue
        chosen = candidate
        break

    if chosen is None:
        chosen = candidates[0]
        constraints.append("G1_relaxed_no_free_slot")

    slot = chosen["slot"]

    # G2: stagger clips of the same source across platforms so they do not
    # all land simultaneously and blur attribution.
    for sibling in siblings:
        if abs((slot - sibling).total_seconds()) < CROSS_PLATFORM_STAGGER_MIN * 60:
            slot += timedelta(minutes=CROSS_PLATFORM_STAGGER_MIN)
            constraints.append("G2_cross_platform_stagger")
            break

    slot += timedelta(minutes=_jitter_minutes(post_id))
    constraints.append("G5_jitter_applied")

    rule = chosen["rule"]
    window_start, window_end = rule["window"]

    rationale = ScheduleRationale(
        rule_ids=[rule["id"]],
        audience_tz="Europe/Istanbul (UTC+3)",
        audience_segment="TR B2B / İK / bordro karar vericileri",
        platform_pattern=rule["rationale_tr"],
        content_factor="; ".join(chosen["factors"]) or "içerik tipi etkisi yok",
        weekday_choice=["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"][slot.weekday()],
        window_local=f"{window_start:%H:%M}-{window_end:%H:%M}",
        chosen_local=slot.isoformat(),
        chosen_utc=slot.astimezone(timezone.utc).isoformat(),
        score=chosen["score"],
        competing_slots=[
            {
                "rule": c["rule"]["id"],
                "slot": c["slot"].strftime("%a %H:%M"),
                "score": c["score"],
            }
            for c in candidates[1:5]
        ],
        constraints_applied=constraints,
        source=chosen["source"],
    )
    return slot, rationale
