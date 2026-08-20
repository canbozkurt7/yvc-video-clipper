"""Static reframing: one crop for the whole clip, no snaps at all.

`config.yaml` has advertised `reframe.mode: dynamic | static` with the
comment "per-shot fixed crop, the safe fallback" since the beginning.
Nothing ever read the key -- the safe fallback did not exist.

It is worth having for a measured reason. Every shot-cut snap the QC pass
has examined reads as a jump cut, because the reframer centres the active
speaker at a fixed crop width: after any cut the subject lands at
essentially the same place and the same size, so the picture jumps while
the subject does not. Removing the snaps removes that class of defect
outright, at the cost of framing shots whose composition the single crop
does not suit.
"""

from __future__ import annotations

from yvc.render.reframe import Sample, build_path


def samples(*specs) -> list[Sample]:
    return [Sample(t=t, x_center=x, shot_id=s) for t, x, s in specs]


def test_static_never_snaps():
    """The whole point: a cut must not move the window."""
    path = build_path(
        samples((0.0, 1500, 0), (1.0, 1500, 0), (2.0, 500, 1), (3.0, 500, 1)),
        source_w=1920, source_h=1080, mode="static",
    )
    assert path.stats["shot_snaps"] == 0
    assert path.stats["snap_times"] == []
    assert path.stats["mode"] == "static"


def test_static_produces_a_single_position():
    path = build_path(
        samples((0.0, 1500, 0), (1.0, 1400, 0), (2.0, 500, 1)),
        source_w=1920, source_h=1080, mode="static",
    )
    xs = {round(x, 3) for _, x in path.breakpoints}
    assert len(xs) == 1


def test_static_stays_inside_the_frame():
    path = build_path(
        samples((0.0, 1900, 0), (1.0, 1900, 0)),
        source_w=1920, source_h=1080, mode="static",
    )
    max_x = 1920 - path.width
    assert all(0 <= x <= max_x for _, x in path.breakpoints)


def test_static_ignores_frames_with_no_detection():
    path = build_path(
        [Sample(t=0.0, x_center=1200, shot_id=0),
         Sample(t=1.0, x_center=None, shot_id=0),
         Sample(t=2.0, x_center=1200, shot_id=0)],
        source_w=1920, source_h=1080, mode="static",
    )
    assert path.breakpoints
    assert path.stats["shot_snaps"] == 0


def test_static_with_no_detections_at_all_centres():
    path = build_path(
        [Sample(t=0.0, x_center=None, shot_id=0)],
        source_w=1920, source_h=1080, mode="static",
    )
    max_x = 1920 - path.width
    assert path.breakpoints[0][1] == max_x / 2


def test_static_records_how_far_the_subject_strays():
    """The cost of holding still has to be visible, not discovered on
    playback: a subject that leaves the window is worse than a jump."""
    path = build_path(
        samples((0.0, 400, 0), (1.0, 400, 0), (2.0, 1600, 1), (3.0, 1600, 1)),
        source_w=1920, source_h=1080, mode="static",
    )
    assert "max_subject_offset_px" in path.stats
    assert path.stats["max_subject_offset_px"] > 0


def test_dynamic_is_unchanged_by_the_new_parameter():
    """The default path must behave exactly as it did before."""
    spec = samples((0.0, 1500, 0), (1.0, 1500, 0), (2.0, 500, 1), (3.0, 500, 1))
    before = build_path(spec, source_w=1920, source_h=1080)
    after = build_path(spec, source_w=1920, source_h=1080, mode="dynamic")
    assert before.breakpoints == after.breakpoints
    assert before.stats["shot_snaps"] == after.stats["shot_snaps"] == 1
