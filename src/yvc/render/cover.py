"""Choose a cover frame worth clicking on.

The previous implementation grabbed whatever frame sat at 15% of the
clip. That is a lottery, and it lost: the speaker is mid-word with his
eyes off-camera as often as not, and because the grab came from the
*rendered* clip it also baked in a caption fragment. A thumbnail reading
"SIRKETLER ICIN. BIN UZERINDE" promises nothing to anyone.

Two changes follow. The frame is now taken from the **source** video, so
no caption or hook overlay is trapped inside it, and it is **chosen**
rather than sampled: candidates across the clip are scored on how good
they look and how animated the speaker is at that instant.

The vocal-energy term is the interesting one. Every other signal here
measures photographic quality -- sharp, well exposed, face large and
sensibly placed -- which is necessary but on its own selects bland
frames, because the blandest moments are also the stillest and therefore
the sharpest. Weighting toward the loudest moments of the clip biases
selection to where the speaker is emphatic: gesturing, leaning in,
mid-point rather than mid-pause. It is the cheapest available proxy for
"interesting" and it needs no extra model.

Nothing here invents a promise. The hook line drawn onto the cover is
the same line the clip opens with, and where the selection stage
suppressed that overlay as dishonest, the cover carries no text either.
"""

from __future__ import annotations

import contextlib
import math
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from yvc.bootstrap import child_env
from yvc.render.facetrack import DEFAULT_MODEL, stream_frames
from yvc.render.fonts import resolve_font
from yvc.render.subtitles import LAYOUTS, hex_to_ass
from yvc.turkish.casing import tr_upper

#: Weighted so no single term can carry a frame alone. A tack-sharp shot
#: of the back of someone's head still loses.
WEIGHTS = {
    # Eyes carry the most weight of any single term. A closed-eye frame is
    # not a "slightly worse" thumbnail, it is an unusable one: the subject
    # reads as asleep or exasperated regardless of what the clip says. At
    # 50 fps a blink spans several candidate frames, so this happens often
    # enough that it has to be scored, not hoped away.
    "eyes": 0.20,
    "face": 0.14,
    "sharpness": 0.13,
    # Is the mouth actually visible, or is a hand in front of it? This
    # footage is full of hand-to-face gestures, and they defeat the
    # movement signal below: a hand crossing the mouth looks exactly like
    # talking to a frame-difference metric.
    "mouth_clear": 0.11,
    # Whether the visible face is the one talking. Panel footage cuts to
    # reaction shots constantly, and a cover showing the person who is
    # *listening* while the caption quotes a claim attributes the claim to
    # the wrong face. Speaker attribution (stage 4) would settle this, but
    # it produced no output for this source, so mouth movement stands in.
    "speaking": 0.08,
    "energy": 0.11,
    "frontal": 0.11,
    "exposure": 0.07,
    "thirds": 0.03,
    "size_sanity": 0.02,
}


@dataclass
class Candidate:
    t: float
    raw: dict[str, float] = field(default_factory=dict)
    normalised: dict[str, float] = field(default_factory=dict)
    total: float = 0.0
    #: (x, y, w, h) in detection coordinates, not source pixels.
    face: tuple[float, float, float, float] | None = None
    #: YuNet's five points: right eye, left eye, nose, right and left
    #: mouth corners, in the same detection coordinates.
    landmarks: list[tuple[float, float]] = field(default_factory=list)
    scale: float = 1.0

    @property
    def has_face(self) -> bool:
        return self.face is not None


# --- signals ---------------------------------------------------------


def audio_rms(
    wav_path: str | Path, start: float, end: float, hop: float = 0.2
) -> list[tuple[float, float]]:
    """Coarse RMS envelope over [start, end] as (t, rms) pairs.

    Reads the 16 kHz mono ASR wav directly. Loudness does not need an
    FFT, so this adds no dependency and runs in milliseconds.
    """
    path = Path(wav_path)
    if not path.exists():
        return []
    with contextlib.closing(wave.open(str(path), "rb")) as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        if wav.getsampwidth() != 2:  # pragma: no cover - pipeline writes s16
            return []
        begin = max(0, int(start * rate))
        count = max(0, int((end - start) * rate))
        if count <= 0 or begin >= wav.getnframes():
            return []
        wav.setpos(begin)
        raw = wav.readframes(count)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if samples.size == 0:
        return []
    step = max(1, int(hop * rate))
    envelope: list[tuple[float, float]] = []
    for index in range(0, samples.size, step):
        window = samples[index:index + step]
        if window.size:
            rms = float(np.sqrt(np.mean(window * window)))
            envelope.append((start + index / rate, rms))
    return envelope


def energy_at(envelope: list[tuple[float, float]], t: float) -> float:
    if not envelope:
        return 0.0
    return min(envelope, key=lambda pair: abs(pair[0] - t))[1]


def sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian. At 50 fps motion blur is common, and a
    blurred subject is the most obvious way a thumbnail looks amateur."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def exposure(gray: np.ndarray) -> float:
    """1.0 for a well-exposed, contrasty frame. Penalises crushed blacks
    and blown highlights, both of which survive re-compression badly."""
    mean = float(gray.mean())
    spread = float(gray.std())
    balance = max(0.0, 1.0 - abs(mean - 124.0) / 124.0)
    contrast = min(1.0, spread / 52.0)
    return balance * 0.6 + contrast * 0.4


def eye_openness(gray: np.ndarray, eyes: list[tuple[float, float]],
                 face_w: float) -> float:
    """Rough open/closed score from the two eye landmarks.

    YuNet gives eye *positions*, not eyelid geometry, so openness has to
    be inferred from the pixels. An open eye contains a pupil: a small
    very dark region against sclera, which makes the patch dark overall
    and high-contrast. A closed eye is an eyelid: skin-toned, smooth,
    low-contrast. Darkness and spread together separate the two far more
    reliably than either alone, since a shadowed face is dark but flat.

    This is a heuristic, not a landmark model. It is scored rather than
    thresholded so a merely squinting frame competes instead of being
    disqualified.
    """
    if not eyes or face_w <= 0:
        return 0.0
    half_w = max(3, int(face_w * 0.13))
    half_h = max(2, int(face_w * 0.075))
    scores: list[float] = []
    for ex, ey in eyes:
        x0, x1 = int(ex - half_w), int(ex + half_w)
        y0, y1 = int(ey - half_h), int(ey + half_h)
        x0, y0 = max(0, x0), max(0, y0)
        patch = gray[y0:y1, x0:x1]
        if patch.size < 12:
            continue
        darkness = 1.0 - float(patch.min()) / 255.0
        spread = min(1.0, float(patch.std()) / 42.0)
        scores.append(0.55 * darkness + 0.45 * spread)
    if not scores:
        return 0.0
    # The worse eye decides. One open and one closed still looks wrong.
    return min(scores)


def mouth_patch(gray: np.ndarray, landmarks: list[tuple[float, float]],
                face_w: float, size: tuple[int, int] = (24, 12)) -> np.ndarray | None:
    """Normalised crop of the mouth, for frame-to-frame comparison.

    Resized to a fixed size so consecutive patches stay comparable even
    as the speaker moves toward or away from the camera.
    """
    if len(landmarks) < 5 or face_w <= 0:
        return None
    (rx, ry), (lx, ly) = landmarks[3], landmarks[4]
    cx, cy = (rx + lx) / 2, (ry + ly) / 2
    half_w = max(4, int(face_w * 0.30))
    half_h = max(3, int(face_w * 0.18))
    x0, y0 = max(0, int(cx - half_w)), max(0, int(cy - half_h))
    patch = gray[y0:int(cy + half_h), x0:int(cx + half_w)]
    if patch.size < 24:
        return None
    return cv2.resize(patch, size).astype(np.float32)


def mouth_clarity(patch: np.ndarray | None) -> float:
    """1.0 when the mouth region contains a real mouth.

    Measured, not guessed: on this source a hand over the mouth leaves the
    darkest pixel in the patch at ~51/255 with a mean of ~151, while an
    unobstructed mouth reaches 3-10 with a mean near 50. Lips and the oral
    shadow are simply much darker than the back of a hand, and the
    detector itself is no help -- YuNet's confidence moved only between
    0.879 and 0.934 across the whole clip, occluded or not.
    """
    if patch is None or patch.size == 0:
        return 0.0
    darkest = float(patch.min())
    mean = float(patch.mean())
    has_dark = max(0.0, 1.0 - darkest / 255.0)
    not_washed = max(0.0, 1.0 - max(0.0, mean - 110.0) / 145.0)
    return has_dark * not_washed


def frontality(landmarks: list[tuple[float, float]]) -> float:
    """1.0 when the nose sits midway between the eyes.

    A profile shot of someone addressing another panellist is a fine
    video frame and a poor thumbnail, because the subject is not looking
    at the viewer.
    """
    if len(landmarks) < 3:
        return 0.0
    (rx, _), (lx, _), (nx, _) = landmarks[0], landmarks[1], landmarks[2]
    span = abs(lx - rx)
    if span < 1e-6:
        return 0.0
    offset = abs((nx - rx) - (lx - nx)) / span
    return max(0.0, 1.0 - offset)


def thirds(cx: float, frame_w: float) -> float:
    """Reward a face near a rule-of-thirds line rather than dead centre."""
    if frame_w <= 0:
        return 0.0
    position = cx / frame_w
    distance = min(abs(position - 1 / 3), abs(position - 2 / 3))
    return max(0.0, 1.0 - distance / (1 / 6))


def size_sanity(face_h: float, frame_h: float) -> float:
    """A face should own a real share of the frame without becoming a
    nostril close-up. Peaks near a quarter of frame height."""
    if frame_h <= 0:
        return 0.0
    ratio = face_h / frame_h
    return math.exp(-((ratio - 0.26) ** 2) / (2 * 0.11 ** 2))


# --- scoring ---------------------------------------------------------


def normalise(candidates: list[Candidate], key: str) -> None:
    """Min-max within the candidate set.

    Absolute thresholds do not transfer between clips: Laplacian variance
    depends on lens, grain and lighting, so "sharp" only means something
    relative to the other frames of this same clip.
    """
    values = [c.raw.get(key, 0.0) for c in candidates]
    low, high = min(values), max(values)
    span = high - low
    for candidate in candidates:
        value = candidate.raw.get(key, 0.0)
        candidate.normalised[key] = 0.5 if span <= 1e-9 else (value - low) / span


#: Below these raw values a frame is bad in absolute terms, not merely
#: worse than its neighbours.
GATES = {"eyes": 0.40, "frontal": 0.45, "mouth_clear": 0.85}
#: How much of the score survives when a signal is at zero.
GATE_FLOOR = {"eyes": 0.30, "frontal": 0.45, "mouth_clear": 0.15}


def gate_penalty(candidate: Candidate) -> float:
    """Multiplier punishing frames that are bad on an absolute scale.

    The weighted sum alone cannot express this. Min-max normalisation is
    relative by construction, so in a clip where the speaker never once
    faces the camera, the least-profile frame still normalises to 1.0 and
    wins as if it were good. These gates ramp rather than cliff, so a
    slightly-turned head competes normally while a full profile or a
    blink is pushed down the ranking whatever else it has going for it.
    """
    penalty = 1.0
    for key, threshold in GATES.items():
        value = candidate.raw.get(key, 0.0)
        if value < threshold:
            floor = GATE_FLOOR[key]
            # Squared, not linear. A linear ramp barely moved the ranking:
            # a hand-over-mouth frame scoring 0.57 against a 0.80 threshold
            # kept 84% of its score and still won on the strength of its
            # other terms. The point of a gate is that a disqualifying
            # flaw actually disqualifies.
            penalty *= floor + (1.0 - floor) * (value / threshold) ** 2
    if not candidate.has_face:
        # A faceless cover is not a thumbnail for a talking-head clip. Not
        # disqualified outright, since a clip may genuinely cut to a
        # graphic, but it has to win by a wide margin.
        penalty *= 0.35
    return penalty


def score_all(candidates: list[Candidate]) -> list[Candidate]:
    if not candidates:
        return []
    for key in WEIGHTS:
        normalise(candidates, key)
    for candidate in candidates:
        base = sum(
            weight * candidate.normalised.get(key, 0.0)
            for key, weight in WEIGHTS.items()
        )
        candidate.total = base * gate_penalty(candidate)
    return sorted(candidates, key=lambda c: c.total, reverse=True)


def collect_candidates(
    video: str,
    start: float,
    end: float,
    *,
    wav_path: str | Path | None = None,
    fps: int = 4,
    width: int = 640,
    height: int = 360,
    source_w: int = 1920,
    model_path: str = DEFAULT_MODEL,
    score_threshold: float = 0.6,
    lead_in: float = 1.5,
    tail_out: float = 1.0,
) -> list[Candidate]:
    """Sample and measure frames across the clip's span in the source."""
    window_start = start + lead_in
    window_end = max(window_start + 0.5, end - tail_out)
    envelope = audio_rms(wav_path, window_start, window_end) if wav_path else []

    detector = cv2.FaceDetectorYN.create(
        model_path, "", (width, height), score_threshold, 0.3, 5
    )
    detector.setInputSize((width, height))
    scale = width / source_w

    candidates: list[Candidate] = []
    previous_mouth: np.ndarray | None = None
    for index, frame in stream_frames(
        video, window_start, window_end, fps=fps, width=width, height=height
    ):
        t = window_start + index / fps
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, detections = detector.detect(frame)

        face = None
        marks: list[tuple[float, float]] = []
        if detections is not None and len(detections):
            # Largest face: in a two-shot the nearer speaker is the subject.
            best = max(detections, key=lambda d: float(d[2]) * float(d[3]))
            face = (float(best[0]), float(best[1]), float(best[2]), float(best[3]))
            # Columns 4..13 are the five landmark pairs.
            marks = [(float(best[i]), float(best[i + 1])) for i in range(4, 14, 2)]

        if face is not None:
            fx, fy, fw, fh = face
            roi = gray[max(0, int(fy)):int(fy + fh), max(0, int(fx)):int(fx + fw)]
            sharp = sharpness(roi) if roi.size else sharpness(gray)
            face_score = min(1.0, (fw * fh) / (width * height) / 0.09)
            thirds_score = thirds(fx + fw / 2, width)
            sanity = size_sanity(fh, height)
            eyes_score = eye_openness(gray, marks[:2], fw)
            frontal_score = frontality(marks)
            current_mouth = mouth_patch(gray, marks, fw)
            mouth_score = mouth_clarity(current_mouth)
            if current_mouth is not None and previous_mouth is not None:
                speaking = float(np.abs(current_mouth - previous_mouth).mean())
            else:
                speaking = 0.0
            previous_mouth = current_mouth
        else:
            sharp = sharpness(gray)
            face_score = thirds_score = sanity = 0.0
            eyes_score = frontal_score = speaking = mouth_score = 0.0
            previous_mouth = None

        candidates.append(Candidate(
            t=t,
            face=face,
            landmarks=marks,
            scale=scale,
            raw={
                "eyes": eyes_score,
                "face": face_score,
                "sharpness": sharp,
                "mouth_clear": mouth_score,
                "speaking": speaking,
                "energy": energy_at(envelope, t),
                "frontal": frontal_score,
                "exposure": exposure(gray),
                "thirds": thirds_score,
                "size_sanity": sanity,
            },
        ))
    return candidates


def pick(candidates: list[Candidate]) -> Candidate | None:
    ranked = score_all(candidates)
    return ranked[0] if ranked else None


# --- composition -----------------------------------------------------


def crop_box(
    candidate: Candidate, aspect: str, source_w: int, source_h: int
) -> tuple[int, int, int, int]:
    """Crop rectangle in source pixels, framed on the chosen face.

    The headroom is asymmetric on purpose. Centring the face box puts the
    eyes in the middle of the frame, which reads as a mugshot; a composed
    portrait sits the eyes near the upper third.
    """
    target = 9 / 16 if aspect == "9:16" else 16 / 9
    if target <= source_w / source_h:
        crop_h = source_h
        crop_w = int(round(crop_h * target))
    else:
        crop_w = source_w
        crop_h = int(round(crop_w / target))
    crop_w = max(2, min(crop_w, source_w))
    crop_h = max(2, min(crop_h, source_h))

    if candidate.face and candidate.scale > 0:
        fx, fy, fw, fh = candidate.face
        cx = (fx + fw / 2) / candidate.scale
        eye_y = (fy + fh * 0.42) / candidate.scale
        x = int(round(cx - crop_w / 2))
        y = int(round(eye_y - crop_h / 3))
    else:
        x = (source_w - crop_w) // 2
        y = (source_h - crop_h) // 2

    x = max(0, min(x, source_w - crop_w))
    y = max(0, min(y, source_h - crop_h))
    return x, y, crop_w, crop_h


def wrap(text: str, chars_per_line: int, max_lines: int = 3) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if len(trial) > chars_per_line and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines[:max_lines]


def cover_ass(
    hook_text: str,
    *,
    aspect: str,
    accent: str,
    paper: str = "#ffffff",
    ink: str = "#101010",
    font_family: str = "Segoe UI Black",
    chars_per_line: int | None = None,
) -> str:
    """A one-event ASS holding the hook line, reusing the caption stack.

    ffmpeg's ``drawtext`` was rejected here for the same reason it was
    rejected for captions: Turkish copy contains ``:``, ``'`` and ``%``,
    each needing different escaping, and libass already shapes the text
    correctly.
    """
    layout = LAYOUTS[aspect]
    width = chars_per_line or layout["chars_per_line"]
    body = r"\N".join(wrap(tr_upper(hook_text.strip()), width))

    size = int(layout["hook_font_size"] * 1.12)
    x = layout["play_res_x"] // 2
    # Sit the text low, clear of the logo in the top-right corner.
    y = int(layout["play_res_y"] * (0.74 if aspect == "9:16" else 0.78))
    style = (
        f"Style: Cover,{font_family},{size},{hex_to_ass(paper)},"
        f"{hex_to_ass(accent)},{hex_to_ass(ink)},{hex_to_ass(ink)},"
        f"-1,0,0,0,100,100,0,0,1,{layout['outline'] + 2},2,5,"
        f"{layout['margin_x']},{layout['margin_x']},0,1"
    )
    event = (
        "Dialogue: 0,0:00:00.00,0:00:05.00,Cover,,0,0,0,,"
        f"{{\\pos({x},{y})}}{body}"
    )
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {layout['play_res_x']}",
        f"PlayResY: {layout['play_res_y']}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        style,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text",
        event,
    ]) + "\n"


def render(
    *,
    source: str | Path,
    workdir: str | Path,
    candidate: Candidate,
    aspect: str,
    brand: dict,
    source_w: int = 1920,
    source_h: int = 1080,
    hook_text: str = "",
    ffmpeg: str = "ffmpeg",
    logo_path: str | Path | None = None,
) -> str | None:
    """Extract, crop and decorate the chosen frame. Returns its path."""
    workdir = Path(workdir)
    layout = LAYOUTS[aspect]
    out_w, out_h = layout["play_res_x"], layout["play_res_y"]
    x, y, crop_w, crop_h = crop_box(candidate, aspect, source_w, source_h)

    chain = [f"crop={crop_w}:{crop_h}:{x}:{y}", f"scale={out_w}:{out_h}"]

    if hook_text.strip():
        font_file = brand.get("fonts", {}).get("display", "seguibl.ttf")
        fonts_dir = workdir / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)
        target = fonts_dir / font_file
        if not target.exists():
            shutil.copy(resolve_font(font_file), target)
        (workdir / "cover.ass").write_text(
            cover_ass(
                hook_text,
                aspect=aspect,
                accent=brand.get("colors", {}).get("accent", "#ff6716"),
                paper=brand.get("colors", {}).get("paper", "#ffffff"),
                ink=brand.get("colors", {}).get("ink", "#101010"),
                font_family=brand.get("fonts", {}).get(
                    "display_family", "Segoe UI Black"
                ),
            ),
            encoding="utf-8",
        )
        # Relative names only. ffmpeg runs with cwd=workdir, so no Windows
        # drive-letter colon ever reaches the filter parser.
        chain.append("ass=filename=cover.ass:fontsdir=fonts")

    # -frames:v is an OUTPUT option. Left before the logo input, ffmpeg
    # reads it as an input option for the logo and refuses to start.
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-ss", f"{candidate.t:.3f}", "-i", str(Path(source).resolve()),
    ]

    if logo_path and Path(logo_path).exists():
        command += ["-i", str(Path(logo_path).resolve())]
        key = "width_px_vertical" if aspect == "9:16" else "width_px_horizontal"
        logo_w = brand.get("logo", {}).get(key, 220)
        margin_x = brand.get("logo", {}).get("margin_x", 56)
        margin_y = brand.get("logo", {}).get("margin_y", 72)
        graph = (
            f"[0:v]{','.join(chain)}[base];"
            f"[1:v]scale={logo_w}:-1[logo];"
            f"[base][logo]overlay=W-w-{margin_x}:{margin_y}:eval=init[out]"
        )
        command += ["-filter_complex", graph, "-map", "[out]"]
    else:
        command += ["-vf", ",".join(chain)]

    command += ["-frames:v", "1", "-q:v", "2", "cover.jpg"]

    proc = subprocess.run(
        command, cwd=str(workdir), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=child_env(), timeout=240,
    )
    return str(workdir / "cover.jpg") if proc.returncode == 0 else None
