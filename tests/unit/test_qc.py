"""The render QC gate.

`classify` is the judgement itself, kept pure so the thresholds can be
argued with directly rather than through a video file. Its gates are
calibrated against a human verdict: the snap that prompted this module
measured a subject shift of 0.122 and was called out as looking wrong, so
that magnitude has to fail.
"""

from __future__ import annotations

from pathlib import Path

from yvc.render.qc import (
    INERT_FRAME_CHANGE,
    MOTIVATED_SCALE,
    MOTIVATED_SHIFT,
    SnapCheck,
    check_clip,
    classify,
)


# --- the human-calibrated boundary ------------------------------------


def test_the_snap_that_prompted_this_module_is_flagged():
    """c01 at 20.67 s: measured 0.1516 / 0.1223 / 0.0275. A viewer called
    it jarring, so the gate must agree."""
    assert classify(0.1516, 0.1223, 0.0275, faces=2) == "jump_cut"


def test_every_measured_snap_in_the_delivered_clips_is_flagged():
    """All six real snaps measured so far, c01 and c03. None of them is a
    motivated cut, which is the finding, not a calibration failure: the
    reframer centres the speaker, so the subject cannot move across a
    cut."""
    measured = [
        (0.1516, 0.1223, 0.0275),
        (0.1310, 0.1037, 0.0705),
        (0.2026, 0.1373, 0.0252),
        (0.2844, 0.1271, 0.0468),
        (0.1798, 0.1140, 0.0306),
        (0.1256, 0.0782, 0.0077),
    ]
    assert all(classify(*m, faces=2) == "jump_cut" for m in measured)


# --- the other verdicts -----------------------------------------------


def test_a_real_move_is_motivated():
    assert classify(0.30, MOTIVATED_SHIFT + 0.05, 0.02, faces=2) == "motivated"


def test_a_size_change_alone_is_motivated():
    """Cutting to a closer shot keeps the subject centred but changes how
    big they are; that reads as an edit."""
    assert classify(0.30, 0.01, MOTIVATED_SCALE + 0.05, faces=2) == "motivated"


def test_a_snap_that_changes_nothing_is_inert():
    assert classify(INERT_FRAME_CHANGE / 2, 0.01, 0.01, faces=2) == "inert"


def test_without_a_face_no_verdict_is_given():
    """Every heuristic here is about the subject. With no subject the
    honest answer is that we cannot tell."""
    assert classify(0.5, 0.4, 0.4, faces=0) == "unreadable"


def test_a_rendered_transition_clears_the_snap():
    """The same measurements that read as a jump cut, once a defocus
    pulse has actually been rendered over the cut. Measured on c01:
    sharpness falls from 250 to 5 across the pulse."""
    assert classify(0.1516, 0.1223, 0.0275, faces=2, dip_ratio=5 / 250) == \
        "transitioned"


def test_a_transition_is_judged_on_the_frames_not_on_config():
    """A `snap_transition: true` setting is not evidence. The first
    version of the pulse fired 0.5 s early on every padded clip while the
    flag still read true, so only the encoded frames can settle it."""
    assert classify(0.1516, 0.1223, 0.0275, faces=2, dip_ratio=0.72) == "jump_cut"


def test_transitioned_is_not_flagged():
    assert not SnapCheck(t=1.0, verdict="transitioned").flagged


def test_flagged_covers_exactly_the_two_bad_verdicts():
    assert SnapCheck(t=1.0, verdict="jump_cut").flagged
    assert SnapCheck(t=1.0, verdict="inert").flagged
    assert not SnapCheck(t=1.0, verdict="motivated").flagged
    assert not SnapCheck(t=1.0, verdict="unreadable").flagged


# --- the file-level checks --------------------------------------------


def test_a_clip_with_no_snaps_passes_without_opening_the_file():
    qc = check_clip("does/not/exist.mp4", [], clip_id="c02")
    assert qc.verdict == "ok"
    assert qc.snaps == []


def test_a_missing_clip_is_reported_rather_than_assumed_fine(tmp_path: Path):
    """render.json claimed 5 of 5 clips rendered while c03's mp4 was not
    on disk at all, because nothing ever looked."""
    qc = check_clip(tmp_path / "absent.mp4", [5.0], clip_id="c03")
    assert qc.verdict == "unreadable"
    assert any("not found" in note for note in qc.notes)


def test_an_unreadable_file_does_not_raise(tmp_path: Path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    qc = check_clip(broken, [1.0], clip_id="cxx")
    assert qc.verdict in ("unreadable", "ok", "review")
