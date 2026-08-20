"""Making a cut look like a cut.

The QC pass established that every shot-cut snap this pipeline produces
reads as a jump cut, and that it cannot be otherwise: the reframer
centres the active speaker at a fixed crop width, so across a cut the
subject lands at the same place and the same size and only the
background moves.

Neither of the obvious answers works on this source. Holding the crop
still (`mode: static`) lets the subject stray 757 px against a 304 px
half-window -- they leave the frame. Avoiding cuts during selection is
impossible for a clip like c03 with four of them in 57 s.

So instead of hiding the cut, mark it: a short defocus pulse centred on
the snap, peaking at the cut. A viewer reads a blur through a cut as an
edit. The same `scale` + `eval=frame` mechanism as the opening variants,
because it is the only filter whose strength can be re-evaluated per
frame -- boxblur and gblur both reject `t` outright.
"""

from __future__ import annotations

from yvc.render.reframe import CropPath, filtergraph, render_variant_video_fragment


def _path() -> CropPath:
    return CropPath(width=608, height=1080, breakpoints=[(0.0, 100.0)])


# --- the pulse itself -------------------------------------------------


def test_no_snaps_means_no_extra_filter():
    """A clip with a single continuous shot must render exactly as before."""
    assert render_variant_video_fragment("plain", snap_times=[]) == ""
    assert render_variant_video_fragment("plain", snap_times=None) == ""


def test_a_snap_produces_a_pulse_at_its_time():
    frag = render_variant_video_fragment("plain", snap_times=[20.67])
    assert "eval=frame" in frag
    assert "20.67" in frag
    # Centred on the cut, so it must be symmetric around it.
    assert "abs(t-20.67)" in frag


def test_every_snap_gets_its_own_pulse():
    frag = render_variant_video_fragment("plain", snap_times=[5.83, 12.0, 31.17])
    for t in ("5.83", "12.0", "31.17"):
        assert f"abs(t-{t})" in frag


def test_the_pulse_width_is_configurable():
    frag = render_variant_video_fragment(
        "plain", {"snap_transition_s": 0.4}, snap_times=[10.0]
    )
    assert "0.2" in frag  # half-width


def test_the_transition_can_be_switched_off():
    frag = render_variant_video_fragment(
        "plain", {"snap_transition": False}, snap_times=[20.67]
    )
    assert frag == ""


# --- composition with the opening variants ----------------------------


def test_an_opening_variant_and_snaps_share_one_scale_pair():
    """Two separate scale-down/up pairs would resample every frame twice
    for the whole clip. One combined expression does the same job once."""
    frag = render_variant_video_fragment(
        "blur_reveal", snap_times=[20.67], out_w=1080, out_h=1920
    )
    assert frag.count("eval=frame") == 1
    assert frag.count("scale=1080:1920") == 1
    assert "abs(t-20.67)" in frag   # the snap pulse
    assert "pow(" in frag           # the opening ramp


def test_sound_sting_shifts_its_pulses_by_the_head_padding():
    """The regression: `tpad` moves the picture 0.5 s later, and the pulse
    expression is evaluated on that padded timeline. Passing the crop
    path's own times straight through fired every pulse half a second
    before its cut -- measured as a sharpness collapse to 38 at 20.77 s
    while the cut sat at 21.17 s."""
    frag = render_variant_video_fragment("sound_sting", snap_times=[10.0])
    assert "tpad=start_mode=clone:start_duration=0.5" in frag
    assert "abs(t-10.5)" in frag
    assert "abs(t-10.0)" not in frag


def test_variants_without_padding_do_not_shift_their_pulses():
    for variant in ("plain", "blur_reveal"):
        frag = render_variant_video_fragment(variant, snap_times=[10.0])
        assert "abs(t-10.0)" in frag, variant


# --- wiring through the graph -----------------------------------------


def test_filtergraph_passes_snaps_through():
    graph = filtergraph(_path(), source_w=1920, snap_times=[20.67])
    assert "abs(t-20.67)" in graph
    assert graph.endswith(",format=yuv420p[vout]")


def test_filtergraph_without_snaps_is_unchanged():
    path = _path()
    assert filtergraph(path, source_w=1920, snap_times=[]) == \
        filtergraph(path, source_w=1920)
