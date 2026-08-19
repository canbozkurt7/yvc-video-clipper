"""Clip rendering: reframe, caption, brand, encode, cover.

Each clip is rendered in its own directory, and **ffmpeg runs with that
directory as its working directory**. This is what lets the filtergraph
reference ``sub.ass`` and ``fonts`` as bare relative names: no drive
letter and no backslash ever reach the filter parser, which sidesteps
ffmpeg's three-level escaping problem entirely instead of trying to
escape through it.

Encoder selection blacklists ``*_nvenc`` outright. This ffmpeg build
advertises NVENC encoders but the machine has no NVIDIA card, so probing
them wastes time and selecting one fails mid-encode with a misleading
error.

A clip that fails does not stop the run. Its error is recorded and the
remaining clips still render, because a partial deliverable beats none.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from yvc.bootstrap import child_env
from yvc.io import read_json, write_json, write_text
from yvc.render.facetrack import detect_track, to_samples
from yvc.render import cover as cover_mod
from yvc.render.fonts import resolve_font
from yvc.render.reframe import CropPath, build_path, filtergraph
from yvc.render.subtitles import Word, build_ass


@dataclass
class RenderResult:
    clip_id: str
    aspect: str
    status: str
    path: str | None = None
    cover: str | None = None
    encoder: str | None = None
    duration_s: float | None = None
    render_s: float | None = None
    crop_stats: dict = field(default_factory=dict)
    error: str | None = None


def probe_encoder(preferred: str = "libx264", ffmpeg: str = "ffmpeg") -> str:
    """Pick a working video encoder.

    NVENC is never probed: advertised by the build, absent in hardware.
    QSV is probed with a real two-second encode because a driver can
    accept initialisation and still fail on actual frames.
    """
    if preferred == "libx264":
        return "libx264"

    if preferred in ("qsv", "auto"):
        try:
            proc = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
                    "-c:v", "h264_qsv", "-f", "null", "-",
                ],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=child_env(), timeout=120,
            )
            if proc.returncode == 0:
                return "h264_qsv"
        except Exception:
            pass
    return "libx264"


def _video_args(encoder: str, cfg: dict) -> list[str]:
    if encoder == "h264_qsv":
        return [
            "-c:v", "h264_qsv",
            "-global_quality", str(cfg.get("qsv_global_quality", 23)),
            "-look_ahead", "0", "-profile:v", "high",
        ]
    return [
        "-c:v", "libx264",
        "-preset", cfg.get("x264_preset", "veryfast"),
        "-crf", str(cfg.get("x264_crf", 20)),
        "-profile:v", "high", "-level", "4.2",
    ]


def _clip_words(transcript: dict, start: float, end: float) -> list[Word]:
    """Word timings re-based to the clip's own timeline."""
    out: list[Word] = []
    for seg in transcript["segments"]:
        if seg["end"] < start or seg["start"] > end:
            continue
        for w in seg.get("words") or []:
            if w["end"] < start or w["start"] > end:
                continue
            out.append(
                Word(
                    text=w["w"],
                    start=max(0.0, w["start"] - start),
                    end=max(0.02, w["end"] - start),
                )
            )
    return out


def render_clip(
    clip: dict,
    *,
    source: Path,
    transcript: dict,
    brand: dict,
    out_root: Path,
    assets: Path,
    cfg: dict,
    encoder: str,
    ffmpeg: str = "ffmpeg",
    wav_path: Path | None = None,
) -> RenderResult:
    clip_id = clip["clip_id"]
    aspect = clip["aspect"]
    start, end = clip["start"], clip["end"]
    duration = end - start
    workdir = out_root / clip_id
    workdir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    try:
        # ffmpeg runs with cwd set to the clip folder so the filtergraph can
        # name sub.ass and fonts relatively. Every path handed to ffmpeg from
        # outside that folder must therefore be absolute, or it resolves
        # against the wrong directory.
        source = Path(source).resolve()
        # Fonts are copied next to the clip so the filtergraph can name the
        # directory relatively. ~300 KB per clip is a fair price for
        # removing an entire class of path-escaping bug.
        fonts_dir = workdir / "fonts"
        fonts_dir.mkdir(exist_ok=True)
        font_file = brand["fonts"]["display"]
        shutil.copy(resolve_font(font_file), fonts_dir / font_file)

        logo_rel = brand["logo"]["dark_bg"]
        logo_path = Path(logo_rel)
        if not logo_path.is_absolute():
            logo_path = Path.cwd() / logo_rel

        # --- captions -------------------------------------------------
        words = _clip_words(transcript, start, end)
        ass = build_ass(
            words,
            aspect=aspect,
            accent=brand["colors"]["accent"],
            ink=brand["colors"]["ink"],
            paper=brand["colors"]["paper"],
            font_family=brand["fonts"]["display_family"],
            hook_text=clip.get("hook_line", ""),
        )
        write_text(workdir / "sub.ass", ass)

        # --- reframe (vertical only) ----------------------------------
        crop_stats: dict = {}
        if aspect == "9:16":
            tracked = detect_track(
                str(source), start, end,
                fps=cfg.get("sample_fps", 6),
                source_w=1920,
                model_path=str(assets / "models" / "face_detection_yunet_2023mar.onnx"),
            )
            path = build_path(
                to_samples(tracked),
                source_w=1920, source_h=1080,
                deadzone_frac=cfg.get("deadzone_frac", 0.045),
                ema_alpha=cfg.get("ema_alpha", 0.12),
                max_pan_px_per_s=cfg.get("max_pan_px_per_s", 40),
                rdp_tolerance_px=cfg.get("rdp_tolerance_px", 6),
            )
            crop_stats = path.stats
            graph = filtergraph(
                path, source_w=1920, out_w=1080, out_h=1920,
                fps=cfg.get("fps", 30),
                logo_width=brand["logo"]["width_px_vertical"],
                logo_x_margin=brand["logo"]["margin_x"],
                logo_y_margin=brand["logo"]["margin_y"],
            )
        else:
            graph = (
                f"[0:v]fps={cfg.get('fps', 30)},scale=1920:1080:flags=bicubic,setsar=1,"
                f"ass=filename=sub.ass:fontsdir=fonts[vsub];\n"
                f"[1:v]scale={brand['logo']['width_px_horizontal']}:-1[logo];\n"
                f"[vsub][logo]overlay=x=W-w-{brand['logo']['margin_x']}:"
                f"y={brand['logo']['margin_y']}:format=auto:eval=init,"
                f"format=yuv420p[vout]"
            )
        write_text(workdir / "fg.txt", graph)

        # --- encode ---------------------------------------------------
        cmd = [
            ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
            "-i", str(logo_path),
            "-filter_complex_script", "fg.txt",
            "-map", "[vout]", "-map", "0:a",
            *_video_args(encoder, cfg),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11" if cfg.get("loudnorm", True) else "anull",
            "-c:a", "aac", "-b:a", cfg.get("audio_bitrate", "128k"),
            "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", "-max_muxing_queue_size", "2048",
            "clip.mp4",
        ]
        proc = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=child_env(), timeout=3600,
        )

        if proc.returncode != 0 and encoder != "libx264":
            # QSV can pass a probe and still fail on real frames. Retrying
            # this one clip on the software encoder makes that a non-event.
            cmd_fallback = list(cmd)
            qsv_start = cmd_fallback.index("-c:v")
            del cmd_fallback[qsv_start : qsv_start + len(_video_args(encoder, cfg))]
            cmd_fallback[qsv_start:qsv_start] = _video_args("libx264", cfg)
            proc = subprocess.run(
                cmd_fallback, cwd=str(workdir), capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=child_env(), timeout=3600,
            )
            encoder = "libx264 (qsv fallback)"

        if proc.returncode != 0:
            return RenderResult(
                clip_id, aspect, "failed",
                error=(proc.stderr or "")[-600:],
                render_s=round(time.time() - started, 1),
            )

        cover = _cover(
            source=source, workdir=workdir, clip=clip, aspect=aspect,
            brand=brand, duration=duration, ffmpeg=ffmpeg, wav_path=wav_path,
        )

        return RenderResult(
            clip_id=clip_id,
            aspect=aspect,
            status="ok",
            path=str(workdir / "clip.mp4"),
            cover=cover,
            encoder=encoder,
            duration_s=round(duration, 2),
            render_s=round(time.time() - started, 1),
            crop_stats=crop_stats,
        )

    except Exception as exc:  # one clip's failure must not end the run
        return RenderResult(
            clip_id, aspect, "failed",
            error=f"{type(exc).__name__}: {exc}",
            render_s=round(time.time() - started, 1),
        )


def _cover(
    *,
    source: Path,
    workdir: Path,
    clip: dict,
    aspect: str,
    brand: dict,
    duration: float,
    ffmpeg: str,
    wav_path: Path | None,
    source_w: int = 1920,
    source_h: int = 1080,
) -> str | None:
    """Choose and compose the cover frame.

    Taken from the source rather than the rendered clip, so no burnt-in
    caption fragment ends up in the thumbnail, and chosen by score rather
    than sampled at a fixed offset. See yvc.render.cover.

    Falls back to the old fixed-offset grab if scoring finds nothing --
    a missing cover would block publishing, and a mediocre thumbnail is
    better than none.
    """
    hook = clip.get("hook_line", "") or ""
    logo = brand.get("logo", {}).get("dark_bg")
    logo_path = Path(logo) if logo else None
    if logo_path and not logo_path.is_absolute():
        logo_path = Path.cwd() / logo_path

    try:
        candidates = cover_mod.collect_candidates(
            str(source), clip["start"], clip["end"],
            wav_path=wav_path, source_w=source_w,
        )
        best = cover_mod.pick(candidates)
    except Exception as exc:  # pragma: no cover - detector/codec surprises
        print(f"[render]   cover scoring failed ({type(exc).__name__}: {exc}); "
              "falling back to a fixed grab")
        best = None

    if best is not None:
        chosen = cover_mod.render(
            source=source, workdir=workdir, candidate=best, aspect=aspect,
            brand=brand, source_w=source_w, source_h=source_h,
            hook_text=hook, ffmpeg=ffmpeg, logo_path=logo_path,
        )
        if chosen:
            offset = best.t - clip["start"]
            print(f"[render]   cover at +{offset:.1f}s "
                  f"(score {best.total:.3f}, {len(candidates)} candidates"
                  f"{', hook overlaid' if hook.strip() else ', no hook text'})")
            return chosen
        print("[render]   cover composition failed; falling back to a fixed grab")

    return _cover_fallback(workdir, duration, ffmpeg)


def _cover_fallback(workdir: Path, duration: float, ffmpeg: str) -> str | None:
    """The original behaviour: one frame at 15%, from the rendered clip."""
    at = min(max(3.5, duration * 0.15), max(3.6, duration - 0.5))
    proc = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-ss", f"{at:.2f}", "-i", "clip.mp4", "-frames:v", "1",
            "-q:v", "2", "cover.jpg",
        ],
        cwd=str(workdir), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=child_env(), timeout=180,
    )
    return str(workdir / "cover.jpg") if proc.returncode == 0 else None


def render_all(
    base: str | Path,
    *,
    config_path: str | Path = "config/config.yaml",
    brand_path: str | Path = "config/brand.json",
    assets_dir: str | Path = "assets",
    only: list[str] | None = None,
) -> dict:
    import yaml

    base = Path(base)
    cfg_all = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    render_cfg = {**cfg_all.get("render", {}), **cfg_all.get("reframe", {})}
    brand = read_json(brand_path)

    clips = read_json(base / "clips.json")["clips"]
    if only:
        clips = [c for c in clips if c["clip_id"] in only]
    transcript = read_json(base / "transcript.json")

    encoder = probe_encoder(render_cfg.get("encoder", "libx264"))
    print(f"[render] encoder: {encoder}")

    out_root = base / "clips"
    results: list[RenderResult] = []
    for clip in clips:
        print(f"[render] {clip['clip_id']} {clip['aspect']} {clip['duration']}s ...")
        result = render_clip(
            clip,
            source=base / "source.mp4",
            transcript=transcript,
            brand=brand,
            out_root=out_root,
            assets=Path(assets_dir),
            cfg=render_cfg,
            encoder=encoder,
            wav_path=base / "audio16k_raw.wav",
        )
        results.append(result)
        if result.status == "ok":
            print(
                f"[render]   ok in {result.render_s}s -> {result.path}"
                + (f"  crop={result.crop_stats.get('mode')}" if result.crop_stats else "")
            )
        else:
            print(f"[render]   FAILED: {(result.error or '')[:200]}")

    payload = {
        "encoder": encoder,
        "ok": sum(1 for r in results if r.status == "ok"),
        "failed": sum(1 for r in results if r.status != "ok"),
        "results": [r.__dict__ for r in results],
    }
    write_json(base / "render.json", payload)
    print(f"[render] {payload['ok']} ok, {payload['failed']} failed")
    return payload


if __name__ == "__main__":
    import sys

    import yvc.bootstrap  # noqa: F401

    render_all(sys.argv[1] if len(sys.argv) > 1 else "work/r39OrneyMDs")
