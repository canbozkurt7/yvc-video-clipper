"""Assignment of the `render_variant` opening-style tag to already-scheduled
clips.

This is deliberately NOT the hook_type mechanism: it runs *after*
scheduling and only ever rewrites a label, never a `start`/`end`/order, so
it cannot break the DP scheduler's non-overlap or min_gap guarantees --
there is no interval here to conflict, only a string. See the plan note
in docs about why `with_exploration`'s "always rebuild through the
scheduler" rule does not apply to this axis.

Phase 1 only: assignment here is deterministic-but-unlearned (hashed by
seed), not multiplier-weighted. Disabled by default, so a clips.json
produced before this feature existed is reproduced exactly.
"""

from __future__ import annotations

from yvc.stages.s07_select import Clip, assign_render_variants


def c(clip_id: str, start: float = 0.0, end: float = 10.0) -> Clip:
    return Clip(
        clip_id=clip_id, aspect="9:16", start=start, end=end,
        duration=end - start, score=50.0, hook_type="contrarian",
        hook_line="h", evidence_quote="", source_segment="seg_x", text="t",
    )


def test_disabled_leaves_every_clip_plain():
    clips = [c("c01"), c("c02"), c("c03")]
    assign_render_variants(
        clips, enabled=False,
        values=["plain", "blur_reveal", "sound_sting"], seed="video-a",
    )
    assert [clip.render_variant for clip in clips] == ["plain"] * 3


def test_default_is_plain_even_without_calling_assignment():
    """The dataclass default has to be safe on its own -- older clips.json
    payloads and any code path that never calls assign_render_variants
    must still read as "plain", not None or ''."""
    assert c("c01").render_variant == "plain"


def test_never_touches_start_end_or_order():
    clips = [c("c01", 0, 10), c("c02", 20, 30), c("c03", 40, 50)]
    before = [(clip.clip_id, clip.start, clip.end) for clip in clips]
    assign_render_variants(
        clips, enabled=True,
        values=["plain", "blur_reveal", "sound_sting"], seed="video-a",
    )
    after = [(clip.clip_id, clip.start, clip.end) for clip in clips]
    assert before == after


def test_deterministic_given_the_same_seed():
    clips_a = [c("c01"), c("c02"), c("c03"), c("c04")]
    clips_b = [c("c01"), c("c02"), c("c03"), c("c04")]
    values = ["plain", "blur_reveal", "sound_sting"]
    assign_render_variants(clips_a, enabled=True, values=values, seed="video-a")
    assign_render_variants(clips_b, enabled=True, values=values, seed="video-a")
    assert [x.render_variant for x in clips_a] == [x.render_variant for x in clips_b]


def test_a_different_seed_can_produce_a_different_assignment():
    values = ["plain", "blur_reveal", "sound_sting"]
    clips_a = [c("c01"), c("c02"), c("c03"), c("c04"), c("c05")]
    clips_b = [c("c01"), c("c02"), c("c03"), c("c04"), c("c05")]
    assign_render_variants(clips_a, enabled=True, values=values, seed="video-a")
    assign_render_variants(clips_b, enabled=True, values=values, seed="video-b")
    a = [x.render_variant for x in clips_a]
    b = [x.render_variant for x in clips_b]
    assert a != b


def test_every_assigned_value_is_one_of_the_configured_values():
    values = ["plain", "blur_reveal", "sound_sting"]
    clips = [c(f"c{i:02d}") for i in range(1, 9)]
    assign_render_variants(clips, enabled=True, values=values, seed="video-a")
    assert all(clip.render_variant in values for clip in clips)


def test_empty_values_list_is_a_no_op():
    clips = [c("c01"), c("c02")]
    assign_render_variants(clips, enabled=True, values=[], seed="video-a")
    assert [clip.render_variant for clip in clips] == ["plain", "plain"]
