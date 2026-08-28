"""A/B verdicts compare the two sides of one clip's render_variant split.

Every row feeding one verdict shares clip content, hook and platform
routing, so each platform contributes exactly one A/B *pair*. That is the
constraint the whole design turns on, and the first version of this code
ignored it: it z-scored the pair, which saturates at +/-0.7071 the moment
the two values differ at all, so magnitude was destroyed and the verdict
reported the sign of simulator noise. The first two tests below pin that
regression shut.
"""

from __future__ import annotations

from yvc.report.analysis import MetricRow, analyze_ab_test

SIM = {
    "hook_retention_3s": "SIMULATED", "completion_rate": "SIMULATED",
    "engagement_rate": "SIMULATED", "ctr": "SIMULATED",
}


def row(
    post_id, platform, variant, render_variant, retention,
    completion=0.50, engagement=0.05, ctr=0.010, ab_group="c01",
):
    return MetricRow(
        post_id=post_id, clip_id="c01", platform=platform, hook_type="contrarian",
        variant=variant, render_variant=render_variant, ab_group=ab_group,
        hook_retention_3s=retention, completion_rate=completion,
        engagement_rate=engagement, ctr=ctr, provenance_detail=dict(SIM),
    )


def _pair(retention_a, retention_b, platforms=("instagram", "tiktok", "youtube")):
    rows = []
    for p in platforms:
        rows.append(row(f"c01a-{p}-A", p, "A", "plain", retention_a))
        rows.append(row(f"c01b-{p}-B", p, "B", "blur_reveal", retention_b))
    return rows


def test_magnitude_survives_a_tiny_difference_is_not_a_huge_one():
    """The regression. Z-scoring made 0.1pp and 30pp identical."""
    tiny = analyze_ab_test(_pair(0.700, 0.701))[0]
    huge = analyze_ab_test(_pair(0.700, 1.000))[0]
    assert abs(tiny.mean_lift) < abs(huge.mean_lift) / 50, (
        "a 0.1pp difference must not register as the same lift as a 30pp one"
    )


def test_an_immaterial_difference_declares_no_winner():
    v = analyze_ab_test(_pair(0.700, 0.701))[0]
    assert v.winner is None
    assert v.material is False
    # And the sentence has to say which bar it failed, not go quiet.
    assert "materyallik" in v.sentence_tr


def test_platforms_disagreeing_declares_no_winner():
    """Real c01a/c01b data behaves exactly like this: instagram favours
    plain while tiktok and youtube favour blur_reveal."""
    rows = [
        row("c01a-instagram-A", "instagram", "A", "plain", 0.62),
        row("c01b-instagram-B", "instagram", "B", "blur_reveal", 0.40),
        row("c01a-tiktok-A", "tiktok", "A", "plain", 0.60),
        row("c01b-tiktok-B", "tiktok", "B", "blur_reveal", 0.90),
        row("c01a-youtube-A", "youtube", "A", "plain", 0.61),
        row("c01b-youtube-B", "youtube", "B", "blur_reveal", 0.95),
    ]
    v = analyze_ab_test(rows)[0]
    # Material on average, but one platform runs the other way -- so the
    # honest answer is "inconclusive", not "B won on 2 of 3".
    assert v.material is True
    assert v.winner is None
    assert v.platforms_agreeing == 2 < len(v.platform_lifts) == 3
    assert "aynı yönü" in v.sentence_tr


def test_a_material_unanimous_gap_names_the_winning_side():
    v = analyze_ab_test(_pair(0.60, 0.85))[0]
    assert v.winner == "B"
    assert v.render_variant_a == "plain"
    assert v.render_variant_b == "blur_reveal"
    assert v.platforms_agreeing == 3 == len(v.platform_lifts)
    assert v.mean_lift > 0
    assert "blur_reveal" in v.sentence_tr
    assert v.confidence == "simulated"
    assert any("SİMÜLE" in c for c in v.caveats)


def test_a_wins_when_the_lift_runs_the_other_way():
    v = analyze_ab_test(_pair(0.85, 0.60))[0]
    assert v.winner == "A"
    assert v.mean_lift < 0


def test_named_top_driver_actually_drove_the_result():
    """The other regression: sorting drivers by abs(contribution) put the
    metric the winner *lost* on at drivers[0], so the verdict read
    "B won. Main factor - 3s retention: 0%"."""
    rows = []
    for p in ("instagram", "tiktok", "youtube"):
        # A better on retention (weight 0.45), B better on everything else
        # by enough to still win overall.
        rows.append(row(f"c01a-{p}-A", p, "A", "plain",
                        0.80, completion=0.30, engagement=0.02, ctr=0.004))
        rows.append(row(f"c01b-{p}-B", p, "B", "blur_reveal",
                        0.60, completion=0.90, engagement=0.09, ctr=0.020))
    v = analyze_ab_test(rows)[0]
    assert v.winner == "B"
    top = v.drivers[0]
    assert top["share"] > 0, "the named main factor must have a nonzero share"
    assert top["metric"] != "hook_retention_3s", (
        "retention is where B lost; it cannot be the headline driver of B's win"
    )
    assert f"%{top['share'] * 100:.0f}" in v.sentence_tr


def test_no_ab_group_produces_no_verdicts():
    rows = [MetricRow(post_id="p1", clip_id="c01", platform="instagram",
                      hook_type="contrarian")]
    assert analyze_ab_test(rows) == []


def test_an_incomplete_pair_is_skipped():
    """Only side A published so far -- nothing safe to say yet."""
    assert analyze_ab_test([row("c01a-instagram-A", "instagram", "A", "plain", 0.7)]) == []


def test_sides_on_disjoint_platforms_are_skipped():
    """Nothing is paired, so a lift would compare two different audiences."""
    rows = [
        row("c01a-instagram-A", "instagram", "A", "plain", 0.70),
        row("c01b-tiktok-B", "tiktok", "B", "blur_reveal", 0.90),
    ]
    assert analyze_ab_test(rows) == []


def test_zero_on_the_a_side_is_excluded_not_treated_as_infinite_lift():
    rows = _pair(0.60, 0.85)
    for r in rows:
        if r.variant == "A":
            r.ctr = 0.0
    v = analyze_ab_test(rows)[0]
    ctr = next(d for d in v.drivers if d["metric"] == "ctr")
    assert ctr["mean_lift"] == 0.0
    assert any("oransal karşılaştırmadan çıkarılan" in c for c in v.caveats)


def test_two_ab_groups_each_get_their_own_verdict():
    rows = _pair(0.60, 0.85)
    for p in ("instagram", "tiktok", "youtube"):
        rows.append(row(f"c04a-{p}-A", p, "A", "plain", 0.85, ab_group="c04"))
        rows.append(row(f"c04b-{p}-B", p, "B", "sound_sting", 0.60, ab_group="c04"))
    verdicts = {v.ab_group: v for v in analyze_ab_test(rows)}
    assert set(verdicts) == {"c01", "c04"}
    assert verdicts["c01"].winner == "B"
    assert verdicts["c04"].winner == "A"
