"""Hook performance analysis: which hook type won, which lost, and why.

The "why" is the part that matters and the part most reports skip. A
chart showing contrarian above data_number states *that* one won; it does
not say what drove the gap. Here the difference is decomposed into the
contribution of each component metric, so the verdict reads "62% of the
gap came from 3-second retention" rather than "contrarian performed
better".

Three properties keep it honest:

* **Normalise within platform first.** Pooling raw LinkedIn and TikTok
  numbers would measure platform size, not hook quality.
* **Shrink group means.** A hook type with one observation must not top
  the ranking on noise.
* **Stamp simulated data.** When most of the contributing values are
  simulated, the verdict says so and refuses the word "won".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Composite weights. Retention-at-3s dominates because it measures the
# hook doing its actual job: stopping the scroll. Completion measures
# whether the hook's promise was kept; engagement and clicks are
# downstream consequences and weighted accordingly.
HQS_WEIGHTS = {
    "hook_retention_3s": 0.45,
    "completion_rate": 0.25,
    "engagement_rate": 0.20,
    "ctr": 0.10,
}

SHRINKAGE_K = 5.0


@dataclass
class MetricRow:
    post_id: str
    clip_id: str
    platform: str
    hook_type: str
    variant: str = "A"
    impressions: int = 0
    views_3s: int = 0
    completion_rate: float = 0.0
    engagement_rate: float = 0.0
    ctr: float = 0.0
    hook_retention_3s: float = 0.0
    provenance_detail: dict = field(default_factory=dict)

    def real_fraction(self) -> float:
        fields = [
            "hook_retention_3s", "completion_rate", "engagement_rate", "ctr",
        ]
        if not self.provenance_detail:
            return 0.0
        real = sum(
            1 for f in fields if self.provenance_detail.get(f) == "REAL"
        )
        return real / len(fields)


@dataclass
class HookVerdict:
    winner: str | None
    loser: str | None
    ranking: list[dict]
    drivers: list[dict]
    sentence_tr: str
    confidence: str
    caveats: list[str]
    simulated_share: float


def _zscore_within_platform(rows: list[MetricRow], field_name: str) -> dict[str, float]:
    """Z-score each row's metric against others on the same platform."""
    by_platform: dict[str, list[float]] = {}
    for row in rows:
        by_platform.setdefault(row.platform, []).append(getattr(row, field_name))

    stats = {}
    for platform, values in by_platform.items():
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
        stats[platform] = (mean, math.sqrt(var) if var > 0 else 0.0)

    out = {}
    for row in rows:
        mean, sd = stats[row.platform]
        value = getattr(row, field_name)
        out[row.post_id] = 0.0 if sd == 0 else (value - mean) / sd
    return out


def analyze(rows: list[MetricRow]) -> HookVerdict:
    """Rank hook types and decompose the winner-loser gap."""
    if len(rows) < 2:
        return HookVerdict(
            None, None, [], [],
            "Karşılaştırma için yeterli veri yok.",
            "insufficient_data",
            ["En az iki gönderi gerekli."],
            0.0,
        )

    z: dict[str, dict[str, float]] = {
        field_name: _zscore_within_platform(rows, field_name)
        for field_name in HQS_WEIGHTS
    }

    hqs: dict[str, float] = {}
    for row in rows:
        hqs[row.post_id] = sum(
            weight * z[field_name][row.post_id]
            for field_name, weight in HQS_WEIGHTS.items()
        )

    by_hook: dict[str, list[MetricRow]] = {}
    for row in rows:
        by_hook.setdefault(row.hook_type, []).append(row)

    ranking = []
    for hook_type, group in by_hook.items():
        n = len(group)
        mean = sum(hqs[r.post_id] for r in group) / n
        # Prior mean is 0 by construction of the z-scores, so shrinkage
        # simply pulls small groups toward neutral.
        shrunk = (n * mean) / (n + SHRINKAGE_K)
        ranking.append({
            "hook_type": hook_type,
            "n": n,
            "hqs_mean": round(mean, 4),
            "hqs_shrunk": round(shrunk, 4),
        })
    ranking.sort(key=lambda r: r["hqs_shrunk"], reverse=True)

    if len(ranking) < 2:
        return HookVerdict(
            ranking[0]["hook_type"], None, ranking, [],
            f"Yalnızca tek hook tipi ({ranking[0]['hook_type']}) yayınlandı; "
            "karşılaştırma yapılamıyor.",
            "insufficient_data",
            ["Karşılaştırma için en az iki farklı hook tipi gerekli."],
            _simulated_share(rows),
        )

    winner, loser = ranking[0], ranking[-1]
    win_rows = by_hook[winner["hook_type"]]
    lose_rows = by_hook[loser["hook_type"]]

    # Driver decomposition: how much of the gap does each metric explain?
    contributions = []
    for field_name, weight in HQS_WEIGHTS.items():
        win_z = sum(z[field_name][r.post_id] for r in win_rows) / len(win_rows)
        lose_z = sum(z[field_name][r.post_id] for r in lose_rows) / len(lose_rows)
        contributions.append({
            "metric": field_name,
            "winner_z": round(win_z, 3),
            "loser_z": round(lose_z, 3),
            "contribution": weight * (win_z - lose_z),
        })

    positive = sum(c["contribution"] for c in contributions if c["contribution"] > 0)
    for c in contributions:
        c["share"] = (
            round(c["contribution"] / positive, 4) if positive > 0 and c["contribution"] > 0
            else 0.0
        )
        c["contribution"] = round(c["contribution"], 4)
    contributions.sort(key=lambda c: c["contribution"], reverse=True)

    simulated_share = _simulated_share(rows)
    confidence = "simulated" if simulated_share > 0.5 else "observed"

    labels = {
        "hook_retention_3s": "3 saniye tutunma",
        "completion_rate": "tamamlanma oranı",
        "engagement_rate": "etkileşim oranı",
        "ctr": "tıklama oranı",
    }
    top = contributions[0]
    second = contributions[1] if len(contributions) > 1 else None

    verb = "öne çıktı" if confidence == "simulated" else "kazandı"
    # Phrased to avoid attaching a Turkish case suffix to a numeral.
    # Suffix vowel harmony depends on how the number is *pronounced*
    # (%51 -> "elli bir" -> "'i", %27 -> "yirmi yedi" -> "'si"), which
    # cannot be derived from the digits alone. Restructuring the sentence
    # sidesteps the problem rather than getting it wrong in a deliverable
    # a Turkish reader will see.
    parts = [f"{labels.get(top['metric'], top['metric'])}: %{top['share'] * 100:.0f}"]
    if second and second["share"] > 0.05:
        parts.append(
            f"{labels.get(second['metric'], second['metric'])}: "
            f"%{second['share'] * 100:.0f}"
        )
    sentence = (
        f"{winner['hook_type']} {verb}. Farkı açıklayan başlıca etkenler — "
        + ", ".join(parts)
        + f". En zayıf tip: {loser['hook_type']}."
    )

    caveats = [
        f"Örneklem küçük: kazanan n={winner['n']}, kaybeden n={loser['n']}.",
        "Gönderiler farklı saatlerde ve farklı konularda yayınlandı; "
        "hook tipi tek değişken değil.",
        "Yalnızca yayınlanmayı seçtiğimiz hook tiplerini gözlemliyoruz "
        "(seçim yanlılığı).",
    ]
    if confidence == "simulated":
        caveats.insert(
            0,
            f"Katkıda bulunan değerlerin %{simulated_share * 100:.0f}'ı SİMÜLE. "
            "Bu sonuç yön gösterir, karar vermez.",
        )

    return HookVerdict(
        winner=winner["hook_type"],
        loser=loser["hook_type"],
        ranking=ranking,
        drivers=contributions,
        sentence_tr=sentence,
        confidence=confidence,
        caveats=caveats,
        simulated_share=round(simulated_share, 3),
    )


def _simulated_share(rows: list[MetricRow]) -> float:
    if not rows:
        return 0.0
    return 1.0 - sum(r.real_fraction() for r in rows) / len(rows)
