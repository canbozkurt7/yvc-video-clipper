"""Does the rendered clip actually look right?

Every stage of this pipeline that produces *text* is checked against
evidence: the transcript against diacritic density, the segment
boundaries against a candidate id set, the scored hook against a verbatim
quote, the social copy against the clip's own words. The stage that
produces *video* was checked against one thing only -- whether ffmpeg
exited zero.

That gap had a visible cost. In c01 the reframer teleported the crop 441
px at 20.5 s because the source genuinely cut, from a split-screen
two-shot to a single wide shot. Correct in isolation: interpolating
across a cut looks like a rendering fault. But once both sides are
cropped to 9:16 the same speaker stands at nearly the same place and
size in both, so the viewer never reads it as a cut -- only as the frame
twitching. That is the textbook definition of a jump cut, and it reads
as an error rather than an edit.

Nothing in the pipeline could have noticed. `render.json` recorded
``shot_snaps: 2`` and ``travel_px: 894`` and no code ever read them back.

So this module measures the encoded frames on either side of every snap
and judges the move the way a viewer would -- by what changed on screen,
not by what the crop path intended:

* the subject moved, or changed size    -> the cut is motivated, keep it
* the background changed but the subject did not -> jump cut, flag it
* nothing changed at all                -> the snap bought nothing, flag it

The precedent is `yvc.render.cover`, which already scores a still frame
on nine measured signals and rejects it below a gate. This is the same
idea applied to the clip itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

DEFAULT_MODEL = "assets/models/face_detection_yunet_2023mar.onnx"

#: How far either side of a snap to sample. One reframe sampling interval
#: at 6 fps is 0.167 s, so 0.12 s stays inside the adjacent shot while
#: clearing the encoder's motion blur around the cut.
PROBE_OFFSET_S = 0.12

#: `subject_shift` is the face centre's movement as a fraction of frame
#: width; `scale_change` is |log| of the face-height ratio, so 0.18 is
#: roughly a 20% size change.
#:
#: The shift gate is set from a human verdict, not from theory. The snap
#: that prompted this module measured 0.122 and was called out as looking
#: wrong, so anything at that magnitude has to fail. The subject must
#: cross a fifth of the frame before a cut counts as visually motivated.
#:
#: Calibrated against few examples, and only negative ones -- every snap
#: measured so far is a jump cut. Treat these as provisional: a clip with
#: a genuine speaker change is the missing data point, and until one is
#: measured the gate is more likely to over-flag than under-flag. That is
#: the safer direction for a review signal.
MOTIVATED_SHIFT = 0.20
MOTIVATED_SCALE = 0.18
#: Below this the two frames are effectively the same picture and the
#: snap changed nothing a viewer could see.
INERT_FRAME_CHANGE = 0.04

#: Sharpness at the cut, as a fraction of the sharpness either side. A
#: rendered defocus pulse takes it to roughly 0.02; an unmarked cut sits
#: near 0.7, so the gap is wide and the gate is not delicate.
TRANSITION_DIP = 0.35


@dataclass
class SnapCheck:
    """One shot-cut snap, judged by what it did to the picture."""

    t: float
    frame_change: float = 0.0
    subject_shift: float = 0.0
    scale_change: float = 0.0
    dip_ratio: float = 1.0
    faces: int = 0
    verdict: str = "unreadable"

    @property
    def flagged(self) -> bool:
        return self.verdict in ("jump_cut", "inert")


@dataclass
class ClipQC:
    clip_id: str
    snaps: list[SnapCheck] = field(default_factory=list)
    verdict: str = "ok"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "clip_id": self.clip_id,
            "verdict": self.verdict,
            "notes": self.notes,
            "snaps": [asdict(s) for s in self.snaps],
        }


def classify(
    frame_change: float,
    subject_shift: float,
    scale_change: float,
    *,
    faces: int,
    dip_ratio: float = 1.0,
) -> str:
    """Name what a snap did to the picture.

    Order matters. A rendered transition is checked first and on measured
    evidence, not on configuration: the question is whether the defocus
    pulse actually reached the encoded frames, and a config flag cannot
    answer that -- the first version of the pulse fired half a second
    early on every padded clip and the setting still read `true`.

    "Motivated" comes next, because a real cut to a different framing is
    allowed to be visually loud; only once the subject is known to have
    stayed put does a large background change become evidence *against*
    the cut rather than for it.
    """
    if dip_ratio < TRANSITION_DIP:
        return "transitioned"
    if faces == 0:
        # No face on at least one side: the heuristics below are all
        # about the subject, so refuse to judge rather than guess.
        return "unreadable"
    if subject_shift >= MOTIVATED_SHIFT or scale_change >= MOTIVATED_SCALE:
        return "motivated"
    if frame_change < INERT_FRAME_CHANGE:
        return "inert"
    return "jump_cut"


def _largest_face(frame: np.ndarray, detector) -> tuple[float, float] | None:
    """Centre-x and height of the most prominent face, in pixels."""
    height, width = frame.shape[:2]
    detector.setInputSize((width, height))
    _, detections = detector.detect(frame)
    if detections is None or len(detections) == 0:
        return None
    best = max(detections, key=lambda d: float(d[3]))
    return float(best[0]) + float(best[2]) / 2, float(best[3])


def _sharpness(frame: np.ndarray) -> float:
    """Variance of the Laplacian: how much fine detail survives."""
    grey = cv2.cvtColor(cv2.resize(frame, (192, 341)), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def _frame_at(cap, seconds: float) -> np.ndarray | None:
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000.0)
    ok, frame = cap.read()
    return frame if ok else None


def check_clip(
    clip_path: str | Path,
    snap_times: list[float],
    *,
    clip_id: str = "",
    time_offset: float = 0.0,
    model_path: str = DEFAULT_MODEL,
    probe_offset_s: float = PROBE_OFFSET_S,
) -> ClipQC:
    """Measure every snap in an encoded clip.

    ``time_offset`` shifts the probe times, because an opening variant
    may have padded the head of the video: with ``sound_sting`` the
    picture starts ``sting_delay_s`` later than the crop path thinks, and
    probing without it lands a fifth of a second off on every snap.
    """
    qc = ClipQC(clip_id=clip_id)
    if not snap_times:
        return qc

    path = Path(clip_path)
    if not path.exists():
        qc.verdict = "unreadable"
        qc.notes.append(f"clip not found: {path}")
        return qc

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        qc.verdict = "unreadable"
        qc.notes.append("could not open the encoded clip")
        return qc

    try:
        detector = cv2.FaceDetectorYN.create(model_path, "", (320, 320), 0.6, 0.3, 5)
    except Exception as exc:  # pragma: no cover - model/codec surprises
        cap.release()
        qc.verdict = "unreadable"
        qc.notes.append(f"detector unavailable: {type(exc).__name__}")
        return qc

    for t in snap_times:
        probe = t + time_offset
        before = _frame_at(cap, probe - probe_offset_s)
        after = _frame_at(cap, probe + probe_offset_s)
        if before is None or after is None:
            qc.snaps.append(SnapCheck(t=round(t, 2)))
            continue

        # The frame on the cut itself, to see whether a transition pulse
        # actually rendered there.
        at_cut = _frame_at(cap, probe)
        dip = 1.0
        if at_cut is not None:
            sides = [_sharpness(before), _sharpness(after)]
            reference = max(sides)
            if reference > 1.0:
                dip = _sharpness(at_cut) / reference

        small_a = cv2.cvtColor(cv2.resize(before, (192, 341)), cv2.COLOR_BGR2GRAY)
        small_b = cv2.cvtColor(cv2.resize(after, (192, 341)), cv2.COLOR_BGR2GRAY)
        frame_change = float(
            np.abs(small_a.astype(float) - small_b.astype(float)).mean() / 255.0
        )

        width = before.shape[1]
        face_a = _largest_face(before, detector)
        face_b = _largest_face(after, detector)
        faces = int(face_a is not None) + int(face_b is not None)

        shift = scale = 0.0
        if face_a and face_b:
            shift = abs(face_a[0] - face_b[0]) / max(1.0, width)
            scale = abs(np.log(max(face_a[1], 1.0) / max(face_b[1], 1.0)))

        qc.snaps.append(SnapCheck(
            t=round(t, 2),
            frame_change=round(frame_change, 4),
            subject_shift=round(shift, 4),
            scale_change=round(float(scale), 4),
            dip_ratio=round(dip, 4),
            faces=faces,
            verdict=classify(frame_change, shift, float(scale),
                             faces=2 if faces == 2 else 0, dip_ratio=dip),
        ))

    cap.release()

    flagged = [s for s in qc.snaps if s.flagged]
    if flagged:
        qc.verdict = "review"
        for snap in flagged:
            qc.notes.append(
                f"{snap.verdict} at {snap.t:.1f}s: the picture changed "
                f"{snap.frame_change:.0%} but the subject moved only "
                f"{snap.subject_shift:.0%} of frame width, and no "
                f"transition was rendered over it"
            )
    return qc
