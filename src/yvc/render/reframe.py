"""Speaker-tracking crop path for 9:16 reframing.

The brief rejects a centre crop, so the crop window follows the active
speaker. Two halves: build a smoothed trajectory from sampled face
detections, then express that trajectory in a form ffmpeg can evaluate.

On the smoothing, order matters and each step earns its place:

    deadzone -> EMA -> rate limit -> shot-cut snap -> clamp -> simplify

* **Deadzone** first, because detector jitter is the dominant noise
  source. Without it the frame breathes constantly and no amount of
  filtering downstream looks intentional.
* **Shot-cut snap** is not smoothing but its opposite: at a hard cut the
  window teleports. Interpolating across a cut produces a slide that
  reads as a rendering bug rather than a camera move.
* **Face loss holds position** instead of recentring. Recentring on a
  dropped detection is the single most common source of ugly drift.

On applying it: ``sendcmd`` was rejected. It only produces step changes,
so smooth motion would need one command per frame. Instead the ``crop``
filter's ``x`` option is given an expression evaluated per frame, built
as a flat sum of clipped ramps rather than nested ``if()`` calls -- it is
O(n), order-independent, and exactly piecewise-linear.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Sample:
    """One sampled frame: where the active speaker is, if anyone."""

    t: float
    x_center: float | None  # None when no face was detected
    shot_id: int = 0


@dataclass
class CropPath:
    width: int
    height: int
    breakpoints: list[tuple[float, float]] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def crop_width_for_vertical(source_h: int) -> int:
    """9:16 window width for a given source height, rounded to even.

    H.264 chroma subsampling requires even dimensions; an odd width makes
    the encoder fail or silently pad.
    """
    width = int(round(source_h * 9 / 16))
    return width - (width % 2)


def _shot_anchors(samples: list[Sample], commit_frac: float) -> dict[int, float]:
    """One committed subject position per shot.

    Continuously chasing whichever face looks active makes the frame
    wander: in a two-shot the active-speaker signal flickers between
    people and the filter follows it. A human editor instead picks a
    subject for the duration of a shot and reframes at the cut.

    The anchor is the median of detections in the shot, and detections
    far from that median (the other person) are excluded before taking
    it, so a brief look at the second speaker does not drag the frame.
    """
    by_shot: dict[int, list[float]] = {}
    for sample in samples:
        if sample.x_center is not None:
            by_shot.setdefault(sample.shot_id, []).append(sample.x_center)

    anchors: dict[int, float] = {}
    for shot_id, values in by_shot.items():
        values = sorted(values)
        median = values[len(values) // 2]
        spread = commit_frac * 1920
        near = [v for v in values if abs(v - median) <= spread]
        chosen = near or values
        anchors[shot_id] = sum(chosen) / len(chosen)
    return anchors


def build_path(
    samples: list[Sample],
    *,
    source_w: int,
    source_h: int,
    deadzone_frac: float = 0.06,
    ema_alpha: float = 0.12,
    max_pan_px_per_s: float = 18.0,
    rdp_tolerance_px: float = 6.0,
    shot_commit: bool = True,
    commit_frac: float = 0.12,
    mode: str = "dynamic",
) -> CropPath:
    """Turn raw detections into a smoothed, simplified crop trajectory.

    ``mode="static"`` holds one window for the whole clip. It exists
    because every snap the QC pass has measured reads as a jump cut: the
    reframer centres the active speaker at a fixed crop width, so across
    a cut the subject lands in the same place at the same size and only
    the background jumps. Holding still removes that entirely, and the
    price -- a subject that drifts within, or out of, the window -- is
    measured into ``max_subject_offset_px`` rather than left to be found
    on playback.
    """
    win_w = crop_width_for_vertical(source_h)
    max_x = max(0, source_w - win_w)
    deadzone = deadzone_frac * source_w

    if mode == "static":
        return _static_path(samples, win_w=win_w, max_x=max_x, source_w=source_w,
                            source_h=source_h)

    if not samples:
        # No detections at all: a centred static crop is the only honest
        # fallback, and it is recorded as such rather than passed off as
        # tracking.
        return CropPath(
            win_w, source_h, [(0.0, max_x / 2)], {"mode": "static_center", "samples": 0}
        )

    anchors_preview = _shot_anchors(samples, commit_frac) if shot_commit else {}

    # Seed from the FIRST SHOT'S anchor, not the first detection. The first
    # frame may catch the wrong speaker, and seeding on it makes the clip
    # open mis-framed and then slide for several seconds while the filter
    # corrects -- which reads as the camera wandering.
    first_shot = samples[0].shot_id
    if shot_commit and first_shot in anchors_preview:
        seed = anchors_preview[first_shot]
    else:
        seed = next(
            (s.x_center for s in samples if s.x_center is not None), source_w / 2
        )
    current = _clamp(seed - win_w / 2, 0, max_x)

    anchors = anchors_preview

    raw: list[tuple[float, float]] = []
    held = 0
    snaps = 0
    # Recorded, not just counted: the QC pass needs to know *where* to look
    # at the encoded clip, and re-deriving a snap from the simplified
    # breakpoints is guesswork once RDP has moved the vertices around.
    snap_times: list[float] = []
    prev_shot = samples[0].shot_id
    prev_t = samples[0].t

    for sample in samples:
        if sample.x_center is None:
            # Hold. Do not recentre -- that is what causes visible drift.
            held += 1
            target = current
        elif shot_commit and sample.shot_id in anchors:
            # Track the shot's committed subject, not whoever moved last.
            target = _clamp(anchors[sample.shot_id] - win_w / 2, 0, max_x)
        else:
            target = _clamp(sample.x_center - win_w / 2, 0, max_x)

        cut = sample.shot_id != prev_shot
        if cut:
            # A cut is a teleport, never a pan.
            current = target
            snaps += 1
            snap_times.append(round(sample.t, 3))
        else:
            if abs(target - current) < deadzone:
                target = current
            proposed = current + ema_alpha * (target - current)

            dt = max(1e-3, sample.t - prev_t)
            max_step = max_pan_px_per_s * dt
            delta = proposed - current
            if abs(delta) > max_step:
                proposed = current + max_step * (1 if delta > 0 else -1)
            current = proposed

        current = _clamp(current, 0, max_x)
        raw.append((sample.t, current))
        prev_shot = sample.shot_id
        prev_t = sample.t

    simplified = _rdp(raw, rdp_tolerance_px)

    return CropPath(
        win_w,
        source_h,
        simplified,
        {
            "mode": "dynamic",
            "samples": len(samples),
            "held_frames": held,
            "shot_snaps": snaps,
            "snap_times": snap_times,
            "shot_commit": shot_commit,
            "shots": len(anchors) if anchors else None,
            "breakpoints": len(simplified),
            "max_x": max_x,
            "travel_px": round(
                sum(abs(b[1] - a[1]) for a, b in zip(simplified, simplified[1:])), 1
            ),
        },
    )


def _static_path(
    samples: list[Sample], *, win_w: int, max_x: float, source_w: int, source_h: int
) -> CropPath:
    """One window for the whole clip, placed on the median subject.

    The median rather than the mean: in a two-hander the detections form
    two clusters, and the mean lands in the gap between the speakers --
    framing neither of them. The median sits on whichever speaker holds
    the clip, which is the one worth framing.
    """
    centres = [s.x_center for s in samples if s.x_center is not None]
    if not centres:
        return CropPath(
            win_w, source_h, [(0.0, max_x / 2)],
            {"mode": "static", "samples": len(samples), "shot_snaps": 0,
             "snap_times": [], "breakpoints": 1, "max_x": max_x,
             "travel_px": 0.0, "max_subject_offset_px": None},
        )

    ordered = sorted(centres)
    median = ordered[len(ordered) // 2]
    x = _clamp(median - win_w / 2, 0, max_x)

    # How far the subject wanders from the centre of a window that never
    # moves. Beyond half the window width they have left the frame.
    offsets = [abs(c - (x + win_w / 2)) for c in centres]

    return CropPath(
        win_w,
        source_h,
        [(0.0, x)],
        {
            "mode": "static",
            "samples": len(samples),
            "held_frames": len(samples) - len(centres),
            "shot_snaps": 0,
            "snap_times": [],
            "shots": len({s.shot_id for s in samples}),
            "breakpoints": 1,
            "max_x": max_x,
            "travel_px": 0.0,
            "max_subject_offset_px": round(max(offsets), 1),
            "subject_left_frame": bool(max(offsets) > win_w / 2),
        },
    )


def crop_expression(path: CropPath, source_w: int, *, precision: int = 2) -> str:
    """Build the ffmpeg ``crop=x`` expression.

    Written as ``x0 + sum((x[i]-x[i-1]) * clip((t-t[i-1])/(t[i]-t[i-1]),0,1))``.
    Each term contributes nothing before its ramp starts and its full
    delta after it ends, so the terms simply add -- no nesting, no
    ordering constraints, and the whole thing stays readable.
    """
    points = path.breakpoints
    if not points:
        return "0"
    if len(points) == 1:
        return str(round(points[0][1], precision))

    terms = [str(round(points[0][1], precision))]
    for (t0, x0), (t1, x1) in zip(points, points[1:]):
        delta = round(x1 - x0, precision)
        if abs(delta) < 10**-precision:
            continue
        span = max(1e-3, t1 - t0)
        terms.append(
            f"({delta})*clip((t-{round(t0, 3)})/{round(span, 3)},0,1)"
        )

    body = "+".join(terms)
    # Final clamp is belt and braces: the trajectory is already clamped,
    # but an expression that can ever exceed the frame makes ffmpeg abort
    # mid-encode rather than degrade.
    return f"min(max({body}\\,0)\\,{source_w - path.width})"


def render_variant_head_pad_s(variant: str, cfg: dict | None = None) -> float:
    """Seconds this variant prepends to the picture, if any.

    Single source of truth, because three things have to agree on it and
    they are in three different files: the `tpad` that shifts the video,
    the snap-pulse times inside the same expression, and the QC probe
    that reads the encoded clip afterwards. The first version got the
    latter two wrong -- pulses fired 0.5 s before their cuts.
    """
    cfg = cfg or {}
    if variant == "sound_sting":
        return float(cfg.get("sting_delay_s", 0.5))
    return 0.0


def _snap_pulse_term(snap_times: list[float] | None, cfg: dict) -> str:
    """A defocus pulse centred on every shot-cut snap, or "" for none.

    Each pulse is a triangle: zero outside its window, peaking exactly at
    the cut. Rising before the cut and falling after is what makes it
    read as a transition rather than a glitch -- the picture is already
    softening when the change happens, so the change lands inside a
    motion the viewer has been prepared for.

    The pulses are summed rather than combined with a max(). Snaps in
    real clips are seconds apart and the window is a quarter-second, so
    they never overlap; a sum of disjoint triangles is the same function
    and expresses far more cheaply in an ffmpeg expression.
    """
    if not snap_times or not cfg.get("snap_transition", True):
        return ""

    width = float(cfg.get("snap_transition_s", 0.24))
    strength = int(cfg.get("snap_transition_strength", 7))
    half = round(width / 2, 4)

    pulses = "+".join(
        f"max(0\\,1-abs(t-{round(t, 3)})/{half})" for t in snap_times
    )
    return f"{strength}*({pulses})"


def render_variant_video_fragment(
    variant: str,
    cfg: dict | None = None,
    *,
    out_w: int = 1080,
    out_h: int = 1920,
    snap_times: list[float] | None = None,
) -> str:
    """The video-side filter chunk for an opening style, or "" for none.

    Returned with a leading comma so it can be concatenated onto an
    existing chain without the caller reasoning about separators; an
    empty return therefore leaves the chain byte-identical, which is what
    keeps ``plain`` a true no-op.

    The defocus is a downscale/upscale pair rather than a blur filter, and
    that choice was forced by measurement rather than preference:
    ``boxblur``'s radius and ``gblur``'s sigma are both evaluated **once
    at filter init**, so ``t`` is not even a defined constant there --
    ffmpeg rejects the expression outright. ``scale`` with ``eval=frame``
    is the one option that re-evaluates per frame, and it does support
    ``t``. Measured sharpness across the ramp is smooth and monotonic
    (2.5 -> 4.4 -> 7.9 -> 35 -> 193 -> 658 Laplacian variance).

    The ease is squared on purpose. A linear ramp spends its whole budget
    in a range where blur is already indistinguishable and then resolves
    over a single frame, which reads as a pop rather than a pull.

    ``t`` is seconds from the clip's own start: the clip is already cut by
    ``-ss``/``-to`` before the graph sees it.

    An unrecognised variant degrades to no-op rather than raising. A
    clips.json written before a variant was renamed must still render.
    """
    cfg = cfg or {}

    lead = ""
    opening = ""
    if variant == "blur_reveal":
        seconds = float(cfg.get("reveal_duration_s", 0.6))
        strength = int(cfg.get("reveal_strength", 11))
        opening = f"{strength}*pow(max(0\\,1-t/{seconds})\\,2)"
    elif variant == "sound_sting":
        # Held flat, then released the instant the sting lands -- here the
        # hard snap is the point. The audio half is built in s08_render,
        # because it needs a second ffmpeg input.
        seconds = float(cfg.get("sting_delay_s", 0.5))
        strength = int(cfg.get("sting_strength", 12))
        opening = f"{strength}*lt(t\\,{seconds})"
        # The picture has to be pushed back by exactly what `adelay` does
        # to the speech, or the whole clip plays out of lip sync -- not
        # just the opening. Delaying only the audio was the first attempt
        # and it desynced all 48 s of it.
        #
        # Padding at the *start* is what keeps them together: the frozen
        # opening frame occupies the sting, and both streams resume real
        # content at the same instant. Captions ride along because
        # `ass` has already burned them in by this point in the chain.
        lead = f"tpad=start_mode=clone:start_duration={seconds},"
    elif variant not in ("plain", ""):
        # Unrecognised variant: no opening, but snap pulses still apply.
        opening = ""

    # The snap transitions are folded into the *same* expression as the
    # opening. Two scale-down/up pairs would resample every frame of the
    # clip twice for no benefit; these are all just terms in one defocus
    # factor, and they never overlap in time anyway.
    # The pulses live in the same expression as the opening, so they see
    # the timeline *after* any head padding this variant applied. The snap
    # times come from the crop path, which knows nothing about that.
    pad = render_variant_head_pad_s(variant, cfg)
    shifted = [t + pad for t in (snap_times or [])]
    snaps = _snap_pulse_term(shifted, cfg)
    terms = [term for term in (opening, snaps) if term]
    if not terms:
        return ""

    factor = f"(1+{'+'.join(terms)})"

    # The 40 px floor keeps the intermediate from collapsing to nothing on
    # a long hold; h=-2 keeps it even, which H.264 chroma requires.
    return (
        f",{lead}scale=w='max(40\\,iw/{factor})':h=-2:eval=frame"
        f",scale={out_w}:{out_h}:flags=bicubic"
    )


def filtergraph(
    path: CropPath,
    *,
    source_w: int,
    out_w: int = 1080,
    out_h: int = 1920,
    fps: int = 30,
    subtitle_file: str = "sub.ass",
    fonts_dir: str = "fonts",
    logo_width: int | None = 200,
    logo_x_margin: int = 56,
    logo_y_margin: int = 72,
    render_variant: str = "plain",
    render_variant_cfg: dict | None = None,
    snap_times: list[float] | None = None,
) -> str:
    """Compose the full filtergraph.

    Written to ``fg.txt`` and passed via ``-filter_complex_script``: a long
    expression would otherwise risk the Windows 32,767-character command
    line, and having it on disk makes the graph a reviewable artifact.

    ``subtitle_file`` and ``fonts_dir`` are deliberately bare relative
    names. ffmpeg runs with its working directory set to the clip folder,
    so no drive letter or backslash ever reaches the filter parser -- which
    sidesteps the ``C\\:/path`` escaping problem entirely rather than
    trying to escape through three levels of unquoting.
    """
    expr = crop_expression(path, source_w)
    chain = (
        f"[0:v]fps={fps},"
        f"crop=w={path.width}:h={path.height}:x='{expr}':y=0,"
        f"scale={out_w}:{out_h}:flags=bicubic,"
        f"setsar=1,"
        f"ass=filename={subtitle_file}:fontsdir={fonts_dir}"
    )

    # The opening effect goes last, after captions and logo, so it treats
    # the finished frame as one image -- blurring the video but leaving a
    # crisp caption floating on top would look like a bug, not a reveal.
    opening = render_variant_video_fragment(
        render_variant, render_variant_cfg, out_w=out_w, out_h=out_h,
        snap_times=snap_times,
    )

    if logo_width:
        return (
            f"{chain}[vsub];\n"
            f"[1:v]scale={logo_width}:-1[logo];\n"
            f"[vsub][logo]overlay=x=W-w-{logo_x_margin}:y={logo_y_margin}:"
            f"format=auto:eval=init{opening},format=yuv420p[vout]"
        )
    return f"{chain}{opening},format=yuv420p[vout]"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _rdp(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker simplification on the (t, x) polyline.

    A 45 s clip sampled at 6 fps is 270 points; this typically reduces it
    to 15-35 breakpoints with no visible difference, which keeps the
    generated expression small.
    """
    if len(points) < 3:
        return list(points)

    start, end = points[0], points[-1]
    t0, x0 = start
    t1, x1 = end
    span = t1 - t0

    worst_index, worst = 0, -1.0
    for index in range(1, len(points) - 1):
        t, x = points[index]
        expected = x0 if span <= 0 else x0 + (x1 - x0) * (t - t0) / span
        deviation = abs(x - expected)
        if deviation > worst:
            worst_index, worst = index, deviation

    if worst <= tolerance:
        return [start, end]

    left = _rdp(points[: worst_index + 1], tolerance)
    right = _rdp(points[worst_index:], tolerance)
    return left[:-1] + right
