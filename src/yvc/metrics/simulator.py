"""Behaviourally plausible metric simulation, explicitly labelled.

Sandboxed and draft posts return little or no analytics, and several
platforms never expose a retention curve at all. Rather than leave the
report empty or quietly invent numbers, missing fields are simulated and
every field carries a provenance flag that propagates all the way into
the report.

Two properties make this honest rather than decorative:

* **Conditioned on reality.** When a platform does return impressions,
  the simulator derives views and engagement *from that number* instead
  of inventing an unrelated one, so a MIXED row stays internally
  consistent.
* **Deterministic.** The seed is derived from the post id, so re-running
  the pipeline reproduces identical numbers. Idempotency is a hard
  requirement, and a simulator using real randomness would break it.

The retention model is hook-conditioned in *kind*, not just amount:
contrarian hooks earn comments, data hooks earn saves. That is what makes
the driver decomposition in the report interesting rather than tautological.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

SIMULATOR_VERSION = "sim-1.0.0"

# Fraction of viewers lost in the first 3 seconds, by hook type. Ordering
# encodes the working hypothesis the feedback loop then tests: opening
# with a number promises information but imposes cognitive load, whereas
# opening with a contradiction creates a gap and defers the load.
DROPOFF_3S = {
    "contrarian": 0.24,
    "question": 0.28,
    "curiosity_gap": 0.29,
    "howto": 0.30,
    "data_number": 0.31,
    "social_proof": 0.32,
    "story": 0.33,
}

# Per-platform reach priors: (median impressions, log-sigma, like rate,
# comment rate, save rate, CTR).
PLATFORM_PRIORS = {
    "instagram": (9000, 0.65, 0.041, 0.0035, 0.011, 0.008),
    "tiktok": (14000, 0.85, 0.052, 0.0048, 0.008, 0.004),
    "linkedin": (3800, 0.55, 0.022, 0.0031, 0.004, 0.019),
    "x": (2600, 0.75, 0.018, 0.0022, 0.003, 0.014),
    "youtube": (6200, 0.70, 0.030, 0.0026, 0.006, 0.011),
}

# How mature each window is relative to the terminal (T+30d) value.
WINDOW_MATURITY = {
    "T+1h": {"instagram": 0.18, "tiktok": 0.10, "linkedin": 0.22, "x": 0.35, "youtube": 0.15},
    "T+24h": {"instagram": 0.55, "tiktok": 0.40, "linkedin": 0.80, "x": 0.85, "youtube": 0.50},
    "T+7d": {"instagram": 0.85, "tiktok": 0.78, "linkedin": 0.96, "x": 0.97, "youtube": 0.82},
    "T+30d": {"instagram": 1.0, "tiktok": 1.0, "linkedin": 1.0, "x": 1.0, "youtube": 1.0},
}

ENGAGEMENT_STYLE = {
    "contrarian": {"comments": 1.8, "saves": 0.8, "shares": 1.3},
    "data_number": {"comments": 0.7, "saves": 1.9, "shares": 1.1},
    "story": {"comments": 1.1, "saves": 0.9, "shares": 1.4},
    "howto": {"comments": 0.8, "saves": 1.7, "shares": 1.0},
    "question": {"comments": 1.6, "saves": 0.7, "shares": 0.9},
}

SCHEMA_FIELDS = [
    "impressions", "reach", "views", "views_3s", "avg_view_duration_s",
    "completion_rate", "hook_retention_3s", "dropoff_3s", "retention_curve",
    "likes", "comments", "shares", "saves", "clicks", "conversions",
    "engagement_rate", "ctr",
]


@dataclass
class SimContext:
    post_id: str
    platform: str
    hook_type: str
    duration_s: float
    window: str
    follower_factor: float = 1.0


@dataclass
class SimResult:
    values: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)


class _Rng:
    """Small deterministic PRNG seeded from a string."""

    def __init__(self, seed: str):
        self._state = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16) or 1

    def _next(self) -> float:
        # xorshift64*, adequate for shaping plausible numbers.
        x = self._state
        x ^= (x << 13) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 7
        x ^= (x << 17) & 0xFFFFFFFFFFFFFFFF
        self._state = x
        return ((x * 0x2545F4914F6CDD1D) & 0xFFFFFFFFFFFFFFFF) / 2**64

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self._next()

    def normal(self, mu: float, sigma: float) -> float:
        u1 = max(1e-12, self._next())
        u2 = self._next()
        return mu + sigma * math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)

    def binomial(self, n: int, p: float) -> int:
        """Normal approximation; exact sampling is unnecessary at this scale."""
        if n <= 0 or p <= 0:
            return 0
        mean = n * p
        sd = math.sqrt(max(1e-9, n * p * (1 - p)))
        return max(0, min(n, int(round(self.normal(mean, sd)))))


def retention_curve(hook_type: str, duration_s: float, rng: _Rng) -> list[list[float]]:
    """Two-phase curve: a hook-dependent initial cliff, then exponential decay.

    A small bump near the payoff point is added for narrative hook types,
    which is what a real curve shows when a story lands.
    """
    d0 = DROPOFF_3S.get(hook_type, 0.30)
    lam = 0.9 + 0.6 * (duration_s / 60.0)
    bump = 0.05 if hook_type in ("story", "contrarian") else 0.0
    payoff = 0.78

    points: list[list[float]] = []
    steps = 20
    previous = 1.0
    for i in range(steps + 1):
        frac = i / steps
        if frac == 0:
            value = 1.0
        else:
            base = (1 - d0) * math.exp(-lam * frac)
            if bump:
                base += bump * math.exp(-((frac - payoff) ** 2) / (2 * 0.06**2))
            value = base * rng.uniform(0.97, 1.03)
        # Retention cannot increase; enforce monotonicity after noise.
        value = min(previous, max(0.01, value))
        previous = value
        points.append([round(frac, 3), round(value, 4)])
    return points


def simulate(ctx: SimContext, missing: set[str], real: dict | None = None) -> SimResult:
    """Fill `missing` fields, deriving from `real` values where available."""
    real = real or {}
    rng = _Rng(f"{ctx.post_id}|{ctx.window}|{SIMULATOR_VERSION}")

    median, sigma, like_p, comment_p, save_p, ctr_p = PLATFORM_PRIORS.get(
        ctx.platform, PLATFORM_PRIORS["instagram"]
    )
    maturity = WINDOW_MATURITY.get(ctx.window, WINDOW_MATURITY["T+24h"]).get(
        ctx.platform, 0.6
    )

    values: dict = {}
    provenance: dict = {}

    # Anchor on real impressions when the platform gave us one.
    if "impressions" in real and real["impressions"]:
        impressions = int(real["impressions"])
    else:
        hook_lift = 1.0 + (0.30 - DROPOFF_3S.get(ctx.hook_type, 0.30)) * 2.0
        impressions = int(
            median
            * ctx.follower_factor
            * hook_lift
            * maturity
            * math.exp(rng.normal(0.0, sigma))
        )
        impressions = max(50, impressions)

    curve = retention_curve(ctx.hook_type, ctx.duration_s, rng)
    r3 = _at(curve, min(1.0, 3.0 / max(ctx.duration_s, 3.0)))
    completion = curve[-1][1]
    area = sum(p[1] for p in curve) / len(curve)

    reach = int(impressions * rng.uniform(0.82, 0.94))
    views = int(impressions * rng.uniform(0.70, 0.88))
    views_3s = int(views * r3)

    style = ENGAGEMENT_STYLE.get(ctx.hook_type, {"comments": 1.0, "saves": 1.0, "shares": 1.0})
    likes = rng.binomial(views_3s, like_p)
    comments = rng.binomial(views_3s, comment_p * style["comments"])
    saves = rng.binomial(views_3s, save_p * style["saves"])
    shares = rng.binomial(views_3s, like_p * 0.18 * style["shares"])
    clicks = rng.binomial(views_3s, ctr_p)

    candidates = {
        "impressions": impressions,
        "reach": reach,
        "views": views,
        "views_3s": views_3s,
        "avg_view_duration_s": round(ctx.duration_s * area, 2),
        "completion_rate": round(completion, 4),
        "hook_retention_3s": round(r3, 4),
        "dropoff_3s": round(1 - r3, 4),
        "retention_curve": curve,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "saves": saves,
        "clicks": clicks,
        "conversions": rng.binomial(clicks, 0.045),
        "engagement_rate": round(
            (likes + comments + shares + saves) / max(1, views_3s), 5
        ),
        "ctr": round(clicks / max(1, views_3s), 5),
    }

    for field_name in SCHEMA_FIELDS:
        if field_name in real and real[field_name] is not None:
            values[field_name] = real[field_name]
            provenance[field_name] = "REAL"
        elif field_name in missing:
            values[field_name] = candidates[field_name]
            provenance[field_name] = "SIMULATED"

    return SimResult(values=values, provenance=provenance)


def _at(curve: list[list[float]], frac: float) -> float:
    """Linear interpolation of the retention curve at a time fraction."""
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x0 <= frac <= x1:
            if x1 == x0:
                return y0
            t = (frac - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return curve[-1][1]


def row_provenance(provenance: dict) -> str:
    """Roll per-field flags up to a row-level label."""
    flags = set(provenance.values())
    if flags == {"REAL"}:
        return "REAL"
    if flags == {"SIMULATED"}:
        return "SIMULATED"
    return "MIXED"
