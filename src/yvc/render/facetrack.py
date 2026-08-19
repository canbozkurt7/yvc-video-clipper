"""Face detection, shot segmentation and active-speaker selection.

Detection runs **only inside selected clip ranges, at 6 fps**. Scanning
the whole hour would cost 15+ minutes to produce data that is 95%
discarded -- this is the single biggest performance decision in the
render stage.

Frames are streamed from ffmpeg as raw BGR through a pipe and consumed in
fixed-size chunks, so peak memory is one frame (~700 KB) regardless of
clip length. On a machine with 7.7 GB total that matters more than the
convenience of decoding into an array.

Active-speaker choice, in order of preference:

  1. One face in frame -> that is the speaker.
  2. Two faces -> the one whose mouth region is moving, measured as
     inter-frame difference energy in the lower third of the face box.
     Requires a 2:1 margin before committing; below that the previous
     choice is kept, biased rather than centred, because drifting to the
     midpoint between two people is exactly the centre crop the brief
     rejects.
  3. No faces -> return None and let the crop path hold position.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import cv2
import numpy as np

from yvc.bootstrap import child_env

DEFAULT_MODEL = "assets/models/face_detection_yunet_2023mar.onnx"


@dataclass
class Face:
    x: float
    y: float
    w: float
    h: float
    score: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def mouth_roi(self) -> tuple[int, int, int, int]:
        """Lower third of the face box, where speech shows up."""
        top = int(self.y + self.h * 0.62)
        return int(self.x), top, int(self.w), max(1, int(self.h * 0.34))


@dataclass
class FrameResult:
    index: int
    t: float
    faces: list[Face]
    shot_id: int
    active_cx: float | None
    scale: float  # detection width / source width


def stream_frames(
    video: str,
    start: float,
    end: float,
    *,
    fps: int = 6,
    width: int = 640,
    height: int = 360,
    ffmpeg: str = "ffmpeg",
):
    """Yield (index, BGR ndarray) for a time range.

    ``-ss`` before ``-i`` seeks by keyframe and is fast; the small
    resulting inaccuracy is irrelevant because timings come from the
    frame index and the requested fps, not from decoded PTS.
    """
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", video,
        "-an", "-sn",
        "-vf", f"fps={fps},scale={width}:{height}:flags=bilinear",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]
    frame_bytes = width * height * 3

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child_env()
    )
    try:
        index = 0
        while True:
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            yield index, np.frombuffer(buf, np.uint8).reshape((height, width, 3))
            index += 1
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait(timeout=30)


def detect_track(
    video: str,
    start: float,
    end: float,
    *,
    fps: int = 6,
    width: int = 640,
    height: int = 360,
    source_w: int = 1920,
    model_path: str = DEFAULT_MODEL,
    score_threshold: float = 0.6,
    shot_threshold: float = 28.0,
) -> list[FrameResult]:
    """Detect faces, segment shots, and pick the active speaker per frame."""
    detector = cv2.FaceDetectorYN.create(
        model_path, "", (width, height), score_threshold, 0.3, 5
    )
    detector.setInputSize((width, height))

    results: list[FrameResult] = []
    prev_gray: np.ndarray | None = None
    prev_mouths: dict[int, np.ndarray] = {}
    shot_id = 0
    last_active: float | None = None
    scale = width / source_w

    for index, frame in stream_frames(
        video, start, end, fps=fps, width=width, height=height
    ):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Shot boundary: mean absolute difference against the previous
        # frame. Cheap, and on a multi-camera panel the cuts are hard so
        # a fixed threshold separates them cleanly from motion.
        if prev_gray is not None:
            mad = float(np.mean(cv2.absdiff(gray, prev_gray)))
            if mad > shot_threshold:
                shot_id += 1
                prev_mouths.clear()  # motion across a cut is meaningless
        prev_gray = gray

        count, raw = detector.detect(frame)
        faces: list[Face] = []
        if raw is not None:
            for row in raw:
                faces.append(
                    Face(float(row[0]), float(row[1]), float(row[2]),
                         float(row[3]), float(row[14]))
                )
        faces.sort(key=lambda f: f.w * f.h, reverse=True)

        active = _pick_active(faces, gray, prev_mouths, last_active)
        if active is not None:
            last_active = active

        results.append(
            FrameResult(
                index=index,
                t=index / fps,
                faces=faces,
                shot_id=shot_id,
                # Report in SOURCE coordinates: the crop path works on the
                # full-resolution frame, not the detection proxy.
                active_cx=None if active is None else active / scale,
                scale=scale,
            )
        )

    return results


def _pick_active(
    faces: list[Face],
    gray: np.ndarray,
    prev_mouths: dict[int, np.ndarray],
    last_active: float | None,
) -> float | None:
    """Return the active speaker's x-centre in detection coordinates."""
    if not faces:
        return None
    if len(faces) == 1:
        _remember_mouth(faces[0], gray, prev_mouths, 0)
        return faces[0].cx

    energies: list[float] = []
    for slot, face in enumerate(faces[:2]):
        x, y, w, h = face.mouth_roi
        roi = gray[max(0, y): y + h, max(0, x): x + w]
        previous = prev_mouths.get(slot)
        if previous is not None and previous.shape == roi.shape and roi.size:
            energies.append(float(np.mean(cv2.absdiff(roi, previous))))
        else:
            energies.append(0.0)
        _remember_mouth(face, gray, prev_mouths, slot)

    top, second = sorted(energies, reverse=True)[:2]
    # Require a clear margin. Nodding, laughing and drinking all move the
    # lower face, so a narrow win is not evidence of speech.
    if top > 0 and top >= 2.0 * max(second, 1e-6):
        return faces[energies.index(top)].cx

    if last_active is not None:
        # Stay committed to whoever was last speaking rather than sliding
        # to the midpoint, which would look like a centre crop.
        nearest = min(faces, key=lambda f: abs(f.cx - last_active))
        return 0.65 * nearest.cx + 0.35 * last_active

    return faces[0].cx


def _remember_mouth(
    face: Face, gray: np.ndarray, store: dict[int, np.ndarray], slot: int
) -> None:
    x, y, w, h = face.mouth_roi
    roi = gray[max(0, y): y + h, max(0, x): x + w]
    if roi.size:
        store[slot] = roi.copy()


def to_samples(results: list[FrameResult]):
    """Adapt detection output to the crop-path builder's input."""
    from yvc.render.reframe import Sample

    return [
        Sample(t=r.t, x_center=r.active_cx, shot_id=r.shot_id) for r in results
    ]
