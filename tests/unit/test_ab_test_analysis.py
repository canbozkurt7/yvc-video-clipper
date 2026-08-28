"""A/B verdicts compare the two sides of one clip's render_variant split.

Unlike `analyze()`'s hook-type ranking, every row that feeds one verdict
here shares the same clip content and platform set -- the row-building
tests in test_report_analysis.py (hook_type ranking) are the sibling
suite; this one is scoped to `analyze_ab_test`.
"""

from __future__ import annotations

from yvc.report.analysis import MetricRow, analyze_ab_test


def row(post_id, platform, variant, render_variant, retention, ab_group="c01"):
    return MetricRow(
        post_id=post_id, clip_id="c01", platform=platform, hook_type="contrarian",
        variant=variant, render_variant=render_variant, ab_group=ab_group,
        hook_retention_3s=retention, completion_rate=0.5, engagement_rate=0.05,
        ctr=0.01, provenance_detail={
            "hook_retention_3s": "SIMULATED", "completion_rate": "SIMULATED",
            "engagement_rate": "SIMULATED", "ctr": "SIMULATED",
        },
    )


def test_no_ab_group_produces_no_verdicts():
    rows = [
        MetricRow(post_id="p1", clip_id="c01", platform="instagram",
                   hook_type="contrarian"),
    ]
    assert analyze_ab_test(rows) == []


def test_an_incomplete_pair_is_skipped():
    """Only side A published so far -- nothing safe to say yet."""
    rows = [row("c01a-instagram-A", "instagram", "A", "plain", 0.7)]
    assert analyze_ab_test(rows) == []


def test_a_real_gap_names_the_winning_side():
    rows = [
        row("c01a-instagram-A", "instagram", "A", "plain", 0.60),
        row("c01a-tiktok-A", "tiktok", "A", "plain", 0.58),
        row("c01b-instagram-B", "instagram", "B", "blur_reveal", 0.85),
        row("c01b-tiktok-B", "tiktok", "B", "blur_reveal", 0.83),
    ]
    verdicts = analyze_ab_test(rows)
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.ab_group == "c01"
    assert v.winner == "B"
    assert v.render_variant_a == "plain"
    assert v.render_variant_b == "blur_reveal"
    assert v.n_a == 2 and v.n_b == 2
    assert "blur_reveal" in v.sentence_tr
    assert v.confidence == "simulated"
    assert any("SİMÜLE" in c for c in v.caveats)


def test_two_ab_groups_each_get_their_own_verdict():
    rows = [
        row("c01a-instagram-A", "instagram", "A", "plain", 0.60, ab_group="c01"),
        row("c01b-instagram-B", "instagram", "B", "blur_reveal", 0.80, ab_group="c01"),
        row("c04a-instagram-A", "instagram", "A", "plain", 0.70, ab_group="c04"),
        row("c04b-instagram-B", "instagram", "B", "sound_sting", 0.65, ab_group="c04"),
    ]
    verdicts = {v.ab_group: v for v in analyze_ab_test(rows)}
    assert set(verdicts) == {"c01", "c04"}
    assert verdicts["c01"].winner == "B"
    assert verdicts["c04"].winner == "A"
