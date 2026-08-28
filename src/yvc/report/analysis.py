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
    render_variant: str = "plain"
    ab_group: str | None = None
    lang: str = "tr"
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


# A paired A/B on one clip yields one observation per platform, not a
# distribution. Z-scoring two values against each other saturates at
# +/-0.7071 the moment they differ at all -- the sign survives and the
# magnitude is destroyed -- so the first version of this function
# reported the sign of simulator noise as a creative conclusion, with a
# 0.1pp difference and a 30pp difference producing byte-identical output.
# Raw relative lift keeps the magnitude, and a winner is only named when
# the gap is both material and unanimous across platforms.
AB_MATERIAL_LIFT = 0.05


@dataclass
class VariantVerdict:
    """A vs. B for one render_variant.ab_test split.

    Deliberately not a HookVerdict: that comparison pools posts from
    *different* clips (different content, different hook), which is
    exactly the confound this comparison exists to avoid. Every row here
    comes from the two sides of one `ab_group` -- same transcript, same
    hook, same platforms -- so a gap is attributable to the opening
    effect and not to "which clip was better".

    `mean_lift` is B's composite relative lift over A, averaged over the
    platforms both sides reached. `winner` is None whenever the evidence
    does not clear both bars, and `sentence_tr` then says which bar it
    failed -- an inconclusive A/B is a result, not a gap in the report.
    """

    ab_group: str
    render_variant_a: str
    render_variant_b: str
    n_a: int
    n_b: int
    mean_lift: float
    platform_lifts: list[dict]
    platforms_agreeing: int
    material: bool
    winner: str | None
    drivers: list[dict]
    sentence_tr: str
    confidence: str
    caveats: list[str]
    simulated_share: float


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


METRIC_LABELS_TR = {
    "hook_retention_3s": "3 saniye tutunma",
    "completion_rate": "tamamlanma oranı",
    "engagement_rate": "etkileşim oranı",
    "ctr": "tıklama oranı",
}


def _mean_of(rows: list[MetricRow], field_name: str) -> float:
    return sum(getattr(r, field_name) for r in rows) / len(rows)


def analyze_ab_test(
    rows: list[MetricRow], *, material_lift: float = AB_MATERIAL_LIFT
) -> list[VariantVerdict]:
    """One verdict per `ab_group` present in `rows`: side A vs. side B.

    The two sides of a split share content, hook and platform routing, so
    the only thing left varying is the opening effect -- but that also
    means each platform contributes exactly one A/B *pair*, and a pair is
    not a sample you can z-score. Comparison is therefore raw relative
    lift per platform (magnitude intact), composited with HQS_WEIGHTS and
    averaged over the platforms both sides actually reached.

    A winner is named only when the composite lift clears
    `material_lift` AND every platform agrees on its direction. With
    three platforms a sign test cannot beat p=0.25, so unanimity is the
    strongest claim the data supports; anything weaker is reported as
    inconclusive with the reason, which is more useful to a reader than a
    confident verdict drawn from noise.

    Groups without at least one row on each side, or with no platform in
    common between the sides, are skipped rather than guessed at.
    """
    by_group: dict[str, list[MetricRow]] = {}
    for row in rows:
        if row.ab_group:
            by_group.setdefault(row.ab_group, []).append(row)

    verdicts: list[VariantVerdict] = []
    for ab_group, group_rows in by_group.items():
        sides: dict[str, dict[str, list[MetricRow]]] = {"A": {}, "B": {}}
        for row in group_rows:
            if row.variant in sides:
                sides[row.variant].setdefault(row.platform, []).append(row)
        if not sides["A"] or not sides["B"]:
            continue
        # Only platforms carrying both sides can be paired. A platform
        # that received just one side says nothing about the effect, and
        # averaging its one-sided value in would bias the composite.
        shared = sorted(set(sides["A"]) & set(sides["B"]))
        if not shared:
            continue

        a_rows = [r for p in shared for r in sides["A"][p]]
        b_rows = [r for p in shared for r in sides["B"][p]]

        platform_lifts: list[dict] = []
        metric_lifts: dict[str, list[float]] = {f: [] for f in HQS_WEIGHTS}
        undefined: list[str] = []
        for platform in shared:
            per_metric: dict[str, float] = {}
            composite = 0.0
            for field_name, weight in HQS_WEIGHTS.items():
                a_val = _mean_of(sides["A"][platform], field_name)
                b_val = _mean_of(sides["B"][platform], field_name)
                if a_val <= 0:
                    # A ratio against zero is undefined, not infinite.
                    # Recorded and excluded rather than silently becoming
                    # an enormous lift.
                    lift = 0.0
                    undefined.append(f"{platform}/{field_name}")
                else:
                    lift = (b_val - a_val) / a_val
                per_metric[field_name] = lift
                metric_lifts[field_name].append(lift)
                composite += weight * lift
            platform_lifts.append({
                "platform": platform,
                "composite_lift": round(composite, 4),
                "per_metric": {k: round(v, 4) for k, v in per_metric.items()},
            })

        mean_lift = sum(
            p["composite_lift"] for p in platform_lifts
        ) / len(platform_lifts)
        direction = 1 if mean_lift > 0 else (-1 if mean_lift < 0 else 0)
        agreeing = sum(
            1 for p in platform_lifts
            if direction and p["composite_lift"] * direction > 0
        )
        unanimous = bool(direction) and agreeing == len(platform_lifts)
        material = abs(mean_lift) >= material_lift

        drivers: list[dict] = []
        for field_name, weight in HQS_WEIGHTS.items():
            lifts = metric_lifts[field_name]
            metric_mean = sum(lifts) / len(lifts)
            drivers.append({
                "metric": field_name,
                "mean_lift": round(metric_mean, 4),
                "contribution": round(weight * metric_mean, 4),
            })
        toward = sum(
            d["contribution"] for d in drivers
            if direction and d["contribution"] * direction > 0
        )
        for d in drivers:
            d["share"] = (
                round(d["contribution"] / toward, 4)
                if toward and direction and d["contribution"] * direction > 0
                else 0.0
            )
        # Sorted by how hard each metric pushed *in the direction of the
        # gap*, not by absolute size. Sorting on abs() let the largest
        # metric arguing against the winner land at drivers[0], so the
        # verdict named the one metric the winner lost on, at 0%.
        drivers.sort(key=lambda d: d["contribution"] * (direction or 1), reverse=True)

        simulated_share = _simulated_share(group_rows)
        confidence = "simulated" if simulated_share > 0.5 else "observed"
        variant_a = a_rows[0].render_variant
        variant_b = b_rows[0].render_variant
        winner = ("B" if direction > 0 else "A") if (material and unanimous) else None
        pct = abs(mean_lift) * 100

        if winner:
            winner_variant = variant_a if winner == "A" else variant_b
            loser_variant = variant_b if winner == "A" else variant_a
            top = drivers[0]
            verb = "öne çıktı" if confidence == "simulated" else "kazandı"
            sentence = (
                f"{ab_group}: '{winner_variant}' ({winner}) '{loser_variant}' "
                f"karşısında %{pct:.1f} bileşik fark ile {verb}; "
                f"{agreeing}/{len(platform_lifts)} platformda aynı yön. "
                f"Başlıca etken — "
                f"{METRIC_LABELS_TR.get(top['metric'], top['metric'])}: "
                f"%{top['share'] * 100:.0f}."
            )
        elif not material:
            sentence = (
                f"{ab_group}: '{variant_a}' (A) ile '{variant_b}' (B) arasındaki "
                f"bileşik fark %{pct:.1f}; %{material_lift * 100:.0f} materyallik "
                "eşiğinin altında kaldığı için kazanan ilan edilmiyor."
            )
        else:
            sentence = (
                f"{ab_group}: bileşik fark %{pct:.1f}, ama platformlar aynı yönü "
                f"göstermiyor ({agreeing}/{len(platform_lifts)}). Tek bir çiftte "
                "bu, gürültüden ayrılamaz; kazanan ilan edilmiyor."
            )

        caveats = [
            f"Ölçüm birimi çift: A n={len(a_rows)}, B n={len(b_rows)} gönderi, "
            f"eşleşen {len(platform_lifts)} platformda birer çift.",
            "Aynı klip içeriği, aynı hook, aynı platform seti -- değişen tek şey "
            "açılış efekti. Bu yüzden hook tipi karşılaştırmasından daha az yanlı.",
            f"Kazanan ilanı iki koşulu birlikte arıyor: bileşik farkın "
            f"%{material_lift * 100:.0f} üstünde olması ve tüm platformlarda aynı "
            f"yönü göstermesi. {len(platform_lifts)} platformla işaret testinin "
            "ulaşabileceği en iyi anlamlılık p=0.25'tir; bu bir yön göstergesidir, "
            "istatistiksel kanıt değil.",
        ]
        if undefined:
            caveats.append(
                "A tarafında sıfır olduğu için oransal karşılaştırmadan çıkarılan "
                f"metrikler: {', '.join(sorted(set(undefined))[:4])}."
            )
        if confidence == "simulated":
            caveats.insert(
                0,
                f"Katkıda bulunan değerlerin %{simulated_share * 100:.0f}'ı SİMÜLE. "
                "Bu sonuç yön gösterir, karar vermez.",
            )

        verdicts.append(VariantVerdict(
            ab_group=ab_group,
            render_variant_a=variant_a,
            render_variant_b=variant_b,
            n_a=len(a_rows), n_b=len(b_rows),
            mean_lift=round(mean_lift, 4),
            platform_lifts=platform_lifts,
            platforms_agreeing=agreeing,
            material=material,
            winner=winner,
            drivers=drivers,
            sentence_tr=sentence,
            confidence=confidence,
            caveats=caveats,
            simulated_share=round(simulated_share, 3),
        ))
    return verdicts
