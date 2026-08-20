"""Feedback loop: turn realised performance into hook-scoring priors.

The scorer computes ``S(c) = M(h) * sum_j w_j * s_j(c)`` where ``M(h)`` is
a learned multiplier per hook type. This module computes ``M``.

Four failure modes are designed against explicitly, because a naive
"boost whatever won" loop hits all of them:

* **Cold start** -- an unseen hook type must be treated as exactly
  neutral, not as bad and not as random. Shrinkage gives this for free:
  ``n_eff = 0`` implies ``M = 1.0``.
* **Small samples** -- one lucky clip should not crown a hook type. The
  shrinkage factor ``n/(n+k)`` keeps a single observation near neutral.
* **Runaway convergence** -- the real risk. If the multiplier were
  unbounded, the winning hook type would be picked forever and the others
  would never accumulate evidence. Three independent guards prevent it:
  the multiplier is clipped to [0.80, 1.25], Thompson sampling gives
  under-observed types periodic high draws, and a hard exploration quota
  reserves 20% of slots for types outside the current top two.
* **Staleness** -- recency decay means a hook type that stops being
  posted drifts back toward neutral rather than holding a verdict from
  months ago.

The bounds matter more than they look. At +/-25% max, a genuinely
excellent clip carrying a "losing" hook type still outranks a mediocre
clip with the "winning" one. The learned signal tilts the ranking; it
never dictates it.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

DEFAULT_TAU_DAYS = 90.0
DEFAULT_K = 8.0
DEFAULT_ETA = 0.35
DEFAULT_BOUNDS = (0.80, 1.25)
DEFAULT_SIGMA0 = 0.6
DEFAULT_EXPLORE_RATIO = 0.20


#: An outcome teaches only if this much of the HQS composite came from
#: real measurements. `hook_retention_3s` alone carries 0.45, so this
#: threshold is the numeric way of saying "nothing is learned without a
#: real retention curve".
MIN_REAL_HQS_WEIGHT = 0.60


@dataclass
class Outcome:
    """One realised post: hook type, normalised performance, age.

    ``real_hqs_weight`` is the share of the HQS weighting that came from
    measured rather than simulated fields. It is a fraction, not a label,
    because the row-level label cannot express what matters here: YouTube
    returns no impression count, so a genuinely measured YouTube row is
    always ``MIXED``. Gating on that label would discard every real
    observation the pipeline will ever collect.
    """

    hook_type: str
    hqs: float          # platform-normalised composite (z-scale, ~0 mean)
    age_days: float
    real_hqs_weight: float = 1.0

    @property
    def teaches(self) -> bool:
        return self.real_hqs_weight >= MIN_REAL_HQS_WEIGHT


def real_hqs_weight(provenance_detail: dict | None, hqs_weights: dict) -> float:
    """Share of the HQS weighting backed by real measurements.

    ``provenance_detail`` maps field name -> REAL | SIMULATED. Fields the
    collector never returned are absent, which counts as simulated.
    """
    if not provenance_detail:
        return 0.0
    return sum(
        weight
        for field_name, weight in hqs_weights.items()
        if provenance_detail.get(field_name) == "REAL"
    )


def teaching_outcomes(outcomes: list[Outcome]) -> list[Outcome]:
    """The subset allowed to move the multipliers."""
    return [o for o in outcomes if o.teaches]


@dataclass
class HookPrior:
    hook_type: str
    n_eff: float
    y_bar: float
    y_hat: float
    sigma: float
    multiplier: float
    sampled_multiplier: float


@dataclass
class HookPriors:
    priors: dict[str, HookPrior] = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    def multiplier(self, hook_type: str, *, sampled: bool = True) -> float:
        prior = self.priors.get(hook_type)
        if prior is None:
            return 1.0  # cold start is exactly neutral
        return prior.sampled_multiplier if sampled else prior.multiplier

    def as_rows(self) -> list[dict]:
        return [p.__dict__ for p in self.priors.values()]


def compute_priors(
    outcomes: list[Outcome],
    hook_types: list[str],
    *,
    tau_days: float = DEFAULT_TAU_DAYS,
    k: float = DEFAULT_K,
    eta: float = DEFAULT_ETA,
    bounds: tuple[float, float] = DEFAULT_BOUNDS,
    sigma0: float = DEFAULT_SIGMA0,
    seed: str = "",
) -> HookPriors:
    """Compute per-hook-type multipliers from realised outcomes."""
    lo, hi = bounds
    priors: dict[str, HookPrior] = {}

    for hook_type in hook_types:
        mine = [o for o in outcomes if o.hook_type == hook_type]

        # Recency weighting: an observation from six months ago counts for
        # about a quarter of a fresh one at tau=90d.
        weights = [math.exp(-o.age_days / tau_days) for o in mine]
        n_eff = sum(weights)

        if n_eff > 0:
            y_bar = sum(w * o.hqs for w, o in zip(weights, mine)) / n_eff
        else:
            y_bar = 0.0

        # Shrink toward the global prior, which is 0 by construction of the
        # z-normalised HQS. This is the cold-start and small-sample guard.
        lam = n_eff / (n_eff + k)
        y_hat = lam * y_bar

        multiplier = _clip(math.exp(eta * y_hat), lo, hi)

        # Posterior width shrinks as evidence accumulates. Rarely-used hook
        # types keep a wide posterior and therefore periodically draw high,
        # which is what makes them get retried without any explicit rule.
        sigma = sigma0 / math.sqrt(n_eff + 1.0)
        draw = _gauss(f"{seed}|{hook_type}", y_hat, sigma)
        sampled = _clip(math.exp(eta * draw), lo, hi)

        priors[hook_type] = HookPrior(
            hook_type=hook_type,
            n_eff=round(n_eff, 3),
            y_bar=round(y_bar, 4),
            y_hat=round(y_hat, 4),
            sigma=round(sigma, 4),
            multiplier=round(multiplier, 4),
            sampled_multiplier=round(sampled, 4),
        )

    return HookPriors(
        priors=priors,
        params={
            "tau_days": tau_days, "k": k, "eta": eta,
            "bounds": list(bounds), "sigma0": sigma0,
            "note": "M = clip(exp(eta * shrunk_mean), bounds); "
                    "shrunk_mean = n/(n+k) * recency_weighted_mean",
        },
    )


def select_with_exploration(
    candidates: list[dict],
    n: int,
    priors: HookPriors,
    *,
    explore_ratio: float = DEFAULT_EXPLORE_RATIO,
    score_key: str = "total",
    hook_key: str = "hook_type",
) -> list[dict]:
    """Pick n clips: mostly greedy, with a reserved exploration quota.

    Without the quota, the top-scoring hook type would monopolise every
    slot and the others would never gather the evidence needed to
    challenge it -- the multiplier would then be self-confirming rather
    than learned. Reserving a fifth of the slots for hook types outside
    the current top two guarantees continued evidence for all of them.
    """
    if not candidates:
        return []

    adjusted = []
    for candidate in candidates:
        hook = candidate.get(hook_key, "")
        adjusted.append({
            **candidate,
            "_adjusted": candidate.get(score_key, 0.0) * priors.multiplier(hook),
        })
    adjusted.sort(key=lambda c: c["_adjusted"], reverse=True)

    ranked = sorted(
        priors.priors.values(), key=lambda p: p.multiplier, reverse=True
    )
    top_two = {p.hook_type for p in ranked[:2]}

    explore_slots = max(1, math.ceil(explore_ratio * n)) if n > 1 else 0
    greedy_slots = n - explore_slots

    chosen: list[dict] = []
    used: set[int] = set()

    for index, candidate in enumerate(adjusted):
        if len(chosen) >= greedy_slots:
            break
        candidate["selected_reason"] = "greedy"
        chosen.append(candidate)
        used.add(index)

    for index, candidate in enumerate(adjusted):
        if len(chosen) >= n:
            break
        if index in used:
            continue
        if candidate.get(hook_key) in top_two:
            continue
        candidate["selected_reason"] = "exploration_quota"
        chosen.append(candidate)
        used.add(index)

    # If too few non-top-two candidates exist, fill the remainder greedily
    # rather than returning short.
    for index, candidate in enumerate(adjusted):
        if len(chosen) >= n:
            break
        if index in used:
            continue
        candidate["selected_reason"] = "greedy_fill"
        chosen.append(candidate)
        used.add(index)

    return chosen


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _gauss(seed: str, mu: float, sigma: float) -> float:
    """Deterministic Gaussian draw.

    Seeded rather than random so a re-run reproduces the same selection.
    Reproducibility is a hard requirement here: the pipeline must be
    re-runnable and produce identical artifacts.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    u1 = max(1e-12, int.from_bytes(digest[0:4], "big") / 2**32)
    u2 = int.from_bytes(digest[4:8], "big") / 2**32
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z
