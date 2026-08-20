"""Render-time opening effects, composed into the same filtergraph string
that already produces `sub.ass` captions and the logo overlay.

The regression this pins is the render-side equivalent of
`test_no_priors_and_neutral_priors_score_identically`: with `render_variant`
absent or "plain", the filtergraph must be byte-identical to what this
stage produced before the feature existed. Everything downstream --
`fg.txt`, the encoded clip -- depends on that string being unchanged.
"""

from __future__ import annotations

from yvc.render.reframe import CropPath, filtergraph, render_variant_video_fragment


def _path() -> CropPath:
    return CropPath(width=1080, height=1080, breakpoints=[(0.0, 100.0)])


def test_plain_fragment_is_empty():
    assert render_variant_video_fragment("plain") == ""


def test_unknown_variant_falls_back_to_empty():
    """An unrecognised value must degrade to no-op, not raise -- a stale
    clips.json from before a variant was renamed must still render."""
    assert render_variant_video_fragment("nonexistent") == ""


def test_blur_reveal_fragment_is_evaluated_per_frame():
    """eval=frame is load-bearing, not decoration: boxblur/gblur evaluate
    their strength once at init and reject `t` outright, so scale is the
    only filter here that can ramp at all."""
    frag = render_variant_video_fragment("blur_reveal", {"reveal_duration_s": 0.6})
    assert frag.startswith(",")
    assert "scale=" in frag
    assert "eval=frame" in frag
    assert "0.6" in frag
    assert "t/" in frag


def test_blur_reveal_eases_rather_than_ramping_linearly():
    frag = render_variant_video_fragment("blur_reveal")
    assert "pow(" in frag


def test_sound_sting_holds_flat_then_releases():
    frag = render_variant_video_fragment("sound_sting", {"sting_delay_s": 0.5})
    assert "eval=frame" in frag
    assert "lt(t" in frag
    assert "0.5" in frag


def test_sound_sting_pads_the_head_by_the_audio_delay():
    """The lip-sync regression, pinned.

    `adelay` shifts the speech; if the picture is not shifted by the same
    amount the entire clip plays out of sync, not just the opening. The
    first version padded the tail instead, which equalised the durations
    and left all 48 s desynced.
    """
    frag = render_variant_video_fragment("sound_sting", {"sting_delay_s": 0.5})
    assert "tpad=start_mode=clone:start_duration=0.5" in frag
    assert "stop_duration" not in frag


def test_sound_sting_pad_matches_the_configured_delay():
    """Two knobs that must never drift apart: whatever delays the audio
    has to pad the video by the same number."""
    frag = render_variant_video_fragment("sound_sting", {"sting_delay_s": 0.8})
    assert "start_duration=0.8" in frag
    assert "lt(t\\,0.8)" in frag


def test_blur_reveal_does_not_pad_at_all():
    """blur_reveal leaves audio alone, so shifting the picture would
    create the very desync sound_sting has to pad to avoid."""
    assert "tpad" not in render_variant_video_fragment("blur_reveal")


def test_fragment_scales_back_to_the_requested_output_size():
    frag = render_variant_video_fragment("blur_reveal", out_w=1920, out_h=1080)
    assert "scale=1920:1080" in frag


def test_filtergraph_with_plain_variant_matches_the_pre_feature_graph():
    """Byte-identical to calling filtergraph() with no variant knowledge
    at all -- the default must not perturb existing output."""
    path = _path()
    baseline = filtergraph(path, source_w=1920)
    with_plain = filtergraph(path, source_w=1920, render_variant="plain")
    assert with_plain == baseline


def test_filtergraph_without_logo_and_plain_variant_is_unchanged():
    path = _path()
    baseline = filtergraph(path, source_w=1920, logo_width=None)
    with_plain = filtergraph(path, source_w=1920, logo_width=None, render_variant="plain")
    assert with_plain == baseline


def test_filtergraph_inserts_the_fragment_before_the_final_format():
    path = _path()
    graph = filtergraph(path, source_w=1920, render_variant="blur_reveal")
    assert "eval=frame" in graph
    assert graph.endswith(",format=yuv420p[vout]")
    # The fragment must land before the final format/[vout], not after --
    # the crop/scale/caption chain still has to run through it.
    assert graph.index("eval=frame") < graph.rindex("format=yuv420p[vout]")


def test_filtergraph_without_logo_still_inserts_the_fragment():
    path = _path()
    graph = filtergraph(
        path, source_w=1920, logo_width=None, render_variant="blur_reveal"
    )
    assert "eval=frame" in graph
    assert graph.endswith(",format=yuv420p[vout]")
