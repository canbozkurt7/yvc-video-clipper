"""Phase 2 of render_variant: a real, controlled A/B split.

`assign_render_variants` (tested separately) spreads effects across
*different* clips, which confounds the effect with the content.
`apply_ab_test` instead clones the same clip content into two sides that
differ only in the opening effect, so any measured gap in the report is
attributable to the edit alone.
"""

from __future__ import annotations

from yvc.stages.s07_select import Clip, apply_ab_test


def c(clip_id: str, score: float = 50.0, aspect: str = "9:16") -> Clip:
    return Clip(
        clip_id=clip_id, aspect=aspect, start=0.0, end=10.0, duration=10.0,
        score=score, hook_type="contrarian", hook_line="h", evidence_quote="",
        source_segment="seg_x", text="klip metni",
    )


def test_disabled_is_a_no_op():
    clips = [c("c01"), c("c02")]
    groups = apply_ab_test(
        clips, enabled=False, count=1, variants=["plain", "blur_reveal"], seed="v",
    )
    assert groups == []
    assert [clip.clip_id for clip in clips] == ["c01", "c02"]


def test_requires_exactly_two_variants():
    clips = [c("c01"), c("c02")]
    groups = apply_ab_test(
        clips, enabled=True, count=1,
        variants=["plain", "blur_reveal", "sound_sting"], seed="v",
    )
    assert groups == []
    assert [clip.clip_id for clip in clips] == ["c01", "c02"]


def test_splits_the_highest_scoring_clip_into_two_clones():
    clips = [c("c01", score=40.0), c("c02", score=90.0), c("c03", score=60.0)]
    groups = apply_ab_test(
        clips, enabled=True, count=1, variants=["plain", "blur_reveal"], seed="v",
    )
    assert len(groups) == 1
    assert groups[0]["group"] == "c02"
    ids = {clip.clip_id for clip in clips}
    # The original high-scoring clip is gone, replaced by its two sides;
    # the untouched clips are exactly as they were.
    assert "c02" not in ids
    assert {"c01", "c03", "c02a", "c02b"} == ids


def test_clones_carry_the_original_content_and_a_distinct_opening():
    clips = [c("c01", score=90.0)]
    apply_ab_test(clips, enabled=True, count=1, variants=["plain", "blur_reveal"], seed="v")
    by_id = {clip.clip_id: clip for clip in clips}
    a, b = by_id["c01a"], by_id["c01b"]

    for side, label, variant in ((a, "A", "plain"), (b, "B", "blur_reveal")):
        assert side.ab_group == "c01"
        assert side.variant_label == label
        assert side.render_variant == variant
        # Everything the viewer's experience of the *content* depends on
        # is untouched -- only the opening effect and identity differ.
        assert side.start == 0.0 and side.end == 10.0
        assert side.hook_type == "contrarian"
        assert side.text == "klip metni"


def test_prefers_vertical_clips_when_both_aspects_are_present():
    clips = [c("c01", score=95.0, aspect="16:9"), c("c02", score=50.0, aspect="9:16")]
    groups = apply_ab_test(
        clips, enabled=True, count=1, variants=["plain", "blur_reveal"], seed="v",
    )
    assert groups[0]["group"] == "c02"


def test_deterministic_given_the_same_seed():
    clips_a = [c("c01", score=90.0)]
    clips_b = [c("c01", score=90.0)]
    apply_ab_test(clips_a, enabled=True, count=1, variants=["plain", "blur_reveal"], seed="v")
    apply_ab_test(clips_b, enabled=True, count=1, variants=["plain", "blur_reveal"], seed="v")
    notes_a = next(n for clip in clips_a for n in clip.notes if "ab_test pair id" in n)
    notes_b = next(n for clip in clips_b for n in clip.notes if "ab_test pair id" in n)
    assert notes_a == notes_b


def test_no_clips_is_not_a_crash():
    assert apply_ab_test([], enabled=True, count=1, variants=["plain", "blur_reveal"], seed="v") == []
