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
That is per clip; in bulk it is the opposite. A render where most clips
failed is not a partial deliverable, it is a broken environment, and it
is refused rather than written out -- the same rule
``runtime.min_success_ratio`` applies to the LLM stages.

The flag for handing ffmpeg a filtergraph from a file is probed rather
than hard-coded, because it changed underneath a working pipeline: this
machine's ffmpeg went to 9.0 and ``-filter_complex_script``, removed in
8.0, took every clip in the run down with it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from yvc.bootstrap import child_env
from yvc.io import read_json, write_json, write_text
from yvc.render.facetrack import detect_track, to_samples
from yvc.render import cover as cover_mod
from yvc.render import qc as qc_mod
from yvc.render.fonts import resolve_font
from yvc.render.reframe import (
    CropPath,
    build_path,
    filtergraph,
    render_variant_head_pad_s,
    render_variant_video_fragment,
)
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
    render_variant: str | None = None
    qc: dict = field(default_factory=dict)
    error: str | None = None


def _filtergraph_option_from_probe(output: str) -> str:
    """Read an option probe's output as a choice of flag.

    Split out from the subprocess call so both branches are testable
    against text a real ffmpeg actually printed.
    """
    return (
        "-filter_complex_script"
        if "Unrecognized option" in output
        else "-/filter_complex"
    )


@lru_cache(maxsize=4)
def filtergraph_option(ffmpeg: str = "ffmpeg") -> str:
    """Which flag this ffmpeg takes for a filtergraph read from a file.

    The graph is far too long for a Windows command line, so it lives in
    ``fg.txt``. ``-filter_complex_script`` carried it until ffmpeg 8.0
    removed the option in favour of the generic ``-/filter_complex``
    (available since 7.0). Asking the binary costs one process at the
    start of a render and survives the next rename; assuming costs every
    clip in the run.
    """
    try:
        probe = subprocess.run(
            [ffmpeg, "-hide_banner", "-/filter_complex", "__yvc_option_probe__"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=child_env(), timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        # An ffmpeg that cannot be run at all is a failure the encode
        # will report far better than a probe can.
        return "-/filter_complex"
    return _filtergraph_option_from_probe((probe.stderr or "") + (probe.stdout or ""))


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


def _snap_cfg(cfg: dict) -> dict:
    """The snap-transition knobs, which live under `reframe` in config.

    Kept separate from the `render_variant` block because they describe
    the crop path's cuts, not the clip's opening style -- the two happen
    to share one ffmpeg expression, but they are configured apart.
    """
    return {
        key: cfg[key]
        for key in ("snap_transition", "snap_transition_s",
                    "snap_transition_strength")
        if key in cfg
    }


def _sting_audio_graph(variant: str, cfg: dict, *, loudnorm: bool) -> str | None:
    """Audio half of ``sound_sting``, or None when the variant needs none.

    Returned as filtergraph text rather than an ``-af`` string because it
    consumes a second input file, and ``-af`` cannot reference one. When
    this returns a graph the caller must also add the sting as an input
    and map ``[aout]`` instead of ``0:a`` -- ``-af`` and a mapped
    filtergraph audio output are mutually exclusive in ffmpeg.

    The speech is delayed by the same interval the video holds its blur,
    so the sting lands in the gap and the picture resolves on it.
    """
    if variant != "sound_sting":
        return None

    delay_ms = int(float(cfg.get("sting_delay_s", 0.5)) * 1000)
    gain = float(cfg.get("sting_gain", 0.7))
    norm = "loudnorm=I=-16:TP=-1.5:LRA=11" if loudnorm else "anull"
    return (
        f"[0:a]adelay={delay_ms}|{delay_ms},{norm}[amain];\n"
        f"[2:a]volume={gain}[asting];\n"
        f"[amain][asting]amix=inputs=2:duration=first:dropout_transition=0,"
        f"aresample=48000[aout]"
    )


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
    # Assigned at select time and carried in clips.json; render only reads
    # it. Absent (an older clips.json) means the pre-feature behaviour.
    variant = clip.get("render_variant", "plain")
    variant_cfg = cfg.get("render_variant", {})
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
                # Advertised in config since the beginning and never read
                # until now, so the "safe fallback" it promised did not
                # actually exist.
                mode=cfg.get("mode", "dynamic"),
            )
            crop_stats = path.stats
            if crop_stats.get("subject_left_frame"):
                print(
                    f"[render]   WARNING static crop: the subject strays "
                    f"{crop_stats['max_subject_offset_px']:.0f}px from centre, "
                    f"beyond the {path.width // 2}px half-window -- they leave "
                    f"the frame at some point"
                )
            graph = filtergraph(
                path, source_w=1920, out_w=1080, out_h=1920,
                fps=cfg.get("fps", 30),
                logo_width=brand["logo"]["width_px_vertical"],
                logo_x_margin=brand["logo"]["margin_x"],
                logo_y_margin=brand["logo"]["margin_y"],
                render_variant=variant,
                render_variant_cfg={**variant_cfg, **_snap_cfg(cfg)},
                # Mark every cut instead of trying to hide it. See
                # _snap_pulse_term in yvc.render.reframe.
                snap_times=crop_stats.get("snap_times", []),
            )
        else:
            # Same composition order as the 9:16 path: the opening effect
            # is applied to the finished frame, after captions and logo.
            # 16:9 is never reframed, so it has no crop snaps to mark --
            # only the opening variant applies.
            opening = render_variant_video_fragment(
                variant, variant_cfg, out_w=1920, out_h=1080
            )
            graph = (
                f"[0:v]fps={cfg.get('fps', 30)},scale=1920:1080:flags=bicubic,setsar=1,"
                f"ass=filename=sub.ass:fontsdir=fonts[vsub];\n"
                f"[1:v]scale={brand['logo']['width_px_horizontal']}:-1[logo];\n"
                f"[vsub][logo]overlay=x=W-w-{brand['logo']['margin_x']}:"
                f"y={brand['logo']['margin_y']}:format=auto:eval=init{opening},"
                f"format=yuv420p[vout]"
            )
        # --- opening effect, audio half -------------------------------
        # Only sound_sting needs one. It brings a second input file with
        # it, so the audio moves out of -af and into the filtergraph.
        sting_path: Path | None = None
        audio_graph = _sting_audio_graph(
            variant, variant_cfg, loudnorm=cfg.get("loudnorm", True)
        )
        if audio_graph:
            asset = Path(variant_cfg.get("sting_asset", "assets/sfx/sting_default.wav"))
            if not asset.is_absolute():
                asset = Path.cwd() / asset
            if asset.exists():
                sting_path = asset
                graph = f"{graph};\n{audio_graph}"
            else:
                # Degrade to the visual half rather than failing the clip:
                # a missing bundled asset must not cost a deliverable.
                print(f"[render]   sting asset not found ({asset}); "
                      "rendering the visual half only")
                audio_graph = None
        write_text(workdir / "fg.txt", graph)

        # --- encode ---------------------------------------------------
        cmd = [
            ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
            "-i", str(logo_path),
        ]
        if sting_path:
            cmd += ["-i", str(sting_path)]
        cmd += [
            filtergraph_option(ffmpeg), "fg.txt",
            "-map", "[vout]",
            "-map", "[aout]" if sting_path else "0:a",
            *_video_args(encoder, cfg),
        ]
        if not sting_path:
            # With a mapped filtergraph audio output, -af is rejected;
            # loudnorm already lives inside the graph in that case.
            cmd += [
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=11"
                if cfg.get("loudnorm", True) else "anull",
            ]
        cmd += [
            "-c:a", "aac", "-b:a", cfg.get("audio_bitrate", "128k"),
            "-ar", "48000", "-ac", "2",
        ]
        if sting_path:
            # The two streams no longer end on the same sample: tpad and
            # adelay each round differently. Without this the container
            # advertises a duration a fraction longer than the video.
            cmd += ["-shortest"]
        cmd += [
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

        # --- QC: look at the picture we just produced -----------------
        # A zero exit code says ffmpeg did not crash, not that the clip is
        # watchable. See yvc.render.qc.
        qc_report = qc_mod.check_clip(
            workdir / "clip.mp4",
            crop_stats.get("snap_times", []),
            clip_id=clip_id,
            # A variant may pad the head of the video, putting every snap
            # that much later in the encoded file than in the crop path.
            # Same helper the filter expression uses, so the two cannot
            # drift apart.
            time_offset=render_variant_head_pad_s(variant, variant_cfg),
            model_path=str(assets / "models" / "face_detection_yunet_2023mar.onnx"),
        )
        for note in qc_report.notes:
            print(f"[render]   QC {note}")

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
            render_variant=variant,
            qc=qc_report.as_dict(),
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


class RenderFailureRateError(RuntimeError):
    """Too few clips came out of the encoder to call the render a render."""


def purge_orphan_clip_dirs(
    out_root: Path, keep: set[str], *, enabled: bool
) -> list[str]:
    """Clip directories left behind by a *previous, different* selection.

    A/B splitting renames a clip (c01 -> c01a + c01b), and changing
    ab_test.count or the select thresholds renames others, so the old
    directory keeps a finished clip.mp4 that no longer appears in
    clips.json, render.json or the manifest. Clips are the deliverable:
    somebody listing this folder must not find one more clip than the run
    accounts for, with no way to tell which is current.

    Removal is the default because a clip directory is fully regenerable
    from source.mp4 plus clips.json -- the same reasoning that lets
    retention.purge_source_after_render throw away the much more
    expensive download. Set render.purge_orphan_clip_dirs to false to keep
    them and take the warning instead.

    `keep` must come from the *whole* clips.json, never from a
    `--only`-filtered subset, or a scoped re-render would delete every
    clip it was not asked to touch.
    """
    if not out_root.exists():
        return []
    orphans = sorted(
        d.name for d in out_root.iterdir() if d.is_dir() and d.name not in keep
    )
    for name in orphans:
        if enabled:
            shutil.rmtree(out_root / name, ignore_errors=True)
            print(f"[render] removed orphaned clip dir {name}/ -- not in "
                  "clips.json (regenerable from source + clips.json)")
        else:
            print(f"[render] WARNING orphaned clip dir {name}/ is not in "
                  "clips.json and was left in place")
    return orphans


def render_all(
    base: str | Path,
    *,
    config_path: str | Path = "config/config.yaml",
    brand_path: str | Path = "config/brand.json",
    assets_dir: str | Path = "assets",
    only: list[str] | None = None,
    min_success_ratio: float = 0.6,
) -> dict:
    import yaml

    base = Path(base)
    cfg_all = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    render_cfg = {**cfg_all.get("render", {}), **cfg_all.get("reframe", {})}
    # Nested rather than merged: the variant block has its own keys and
    # flattening it would let a name collide with a reframe setting.
    render_cfg["render_variant"] = cfg_all.get("render_variant", {})
    brand = read_json(brand_path)

    all_clips = read_json(base / "clips.json")["clips"]
    clips = all_clips
    if only:
        clips = [c for c in clips if c["clip_id"] in only]
    transcript = read_json(base / "transcript.json")

    encoder = probe_encoder(render_cfg.get("encoder", "libx264"))
    print(f"[render] encoder: {encoder}")

    out_root = base / "clips"
    # Before rendering, not after: if this run dies half way the folder is
    # still free of clips belonging to a selection that no longer exists.
    orphans = purge_orphan_clip_dirs(
        out_root,
        {c["clip_id"] for c in all_clips},
        enabled=render_cfg.get("purge_orphan_clip_dirs", True),
    )
    results: list[RenderResult] = []
    for clip in clips:
        variant = clip.get("render_variant", "plain")
        print(
            f"[render] {clip['clip_id']} {clip['aspect']} {clip['duration']}s"
            + (f" [{variant}]" if variant != "plain" else "")
            + " ..."
        )
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
        "orphaned_clip_dirs": orphans or None,
        "results": [r.__dict__ for r in results],
    }
    write_json(base / "render.json", payload)
    print(f"[render] {payload['ok']} ok, {payload['failed']} failed")

    # Written first, then judged: render.json is the evidence of what
    # went wrong, and it has to survive the exception that follows.
    attempted = len(results)
    if attempted and min_success_ratio > 0:
        ratio = payload["ok"] / attempted
        if ratio < min_success_ratio:
            first = next(
                (r.error for r in results if r.status != "ok" and r.error), ""
            )
            raise RenderFailureRateError(
                f"render: only {payload['ok']}/{attempted} clips encoded "
                f"({ratio:.0%}), below the {min_success_ratio:.0%} required by "
                f"runtime.min_success_ratio. Clips are the deliverable, so a "
                f"run that mostly failed to produce them is not a partial "
                f"success. First error:\n"
                f"{(first or 'none recorded').strip()[:400]}"
            )
    return payload


if __name__ == "__main__":
    import sys

    import yvc.bootstrap  # noqa: F401

    render_all(sys.argv[1] if len(sys.argv) > 1 else "work/r39OrneyMDs")
