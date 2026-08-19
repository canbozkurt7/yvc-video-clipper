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
) -> CropPath:
    """Turn raw detections into a smoothed, simplified crop trajectory."""
    win_w = crop_width_for_vertical(source_h)
    max_x = max(0, source_w - win_w)
    deadzone = deadzone_frac * source_w

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
            "shot_commit": shot_commit,
            "shots": len(anchors) if anchors else None,
            "breakpoints": len(simplified),
            "max_x": max_x,
            "travel_px": round(
                sum(abs(b[1] - a[1]) for a, b in zip(simplified, simplified[1:])), 1
            ),
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

    if logo_width:
        return (
            f"{chain}[vsub];\n"
            f"[1:v]scale={logo_width}:-1[logo];\n"
            f"[vsub][logo]overlay=x=W-w-{logo_x_margin}:y={logo_y_margin}:"
            f"format=auto:eval=init,format=yuv420p[vout]"
        )
    return f"{chain},format=yuv420p[vout]"


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
