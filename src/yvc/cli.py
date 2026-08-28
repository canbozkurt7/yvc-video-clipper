"""Command-line entry point.

    yvc run <url>              # everything, dry-run publishing by default
    yvc run <url> --only score
    yvc run <url> --from render
    yvc doctor

Resume is fingerprint-based rather than flag-based. Each stage records a
fingerprint over its own version, the config subtree it reads, and its
dependencies' fingerprints. A stage is skipped only when that fingerprint
still matches and its declared outputs exist on disk. So running the same
command twice does no redundant work, while changing a copywriting weight
re-runs copywriting onward without touching the hour-long transcription.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yvc.bootstrap  # noqa: F401  (UTF-8 side effects, must import first)
from yvc.io import read_json, write_json


def _priors_if_enabled(config: dict, announce: str = ""):
    """Load learned hook multipliers, or None when switched off.

    This is the read side of the feedback loop. It was missing entirely:
    priors were computed, persisted and never read back, so every run
    scored as though nothing had ever been measured.
    """
    if not config.get("feedback", {}).get("apply_priors", True):
        return None
    from yvc.db.store import load_priors

    priors = load_priors()
    if announce:
        learned = [p for p in priors.priors.values() if p.multiplier != 1.0]
        print(
            f"[{announce}] hook priors: {len(learned)} learned multiplier(s)"
            if learned else
            f"[{announce}] hook priors: none learned yet, rubric alone"
        )
    return priors


STAGES = [
    "acquire", "transcribe", "turkish", "segment", "score",
    "select", "render", "copywrite", "schedule", "publish",
    "collect", "report", "feedback",
]

# Which config sections invalidate which stage.
CONFIG_KEYS = {
    "acquire": ["source"],
    "transcribe": ["whisper"],
    "turkish": ["turkish"],
    "segment": ["segment", "llm"],
    "score": ["score", "llm", "feedback"],
    "select": ["select", "feedback", "render_variant"],
    "render": ["render", "reframe", "subtitles", "render_variant"],
    "copywrite": ["copy", "llm"],
    "schedule": ["publish"],
    "publish": ["publish"],
    "collect": ["metrics"],
    "report": ["metrics"],
    "feedback": ["feedback"],
}

DEPENDS = {
    "transcribe": ["acquire"],
    "turkish": ["transcribe"],
    "segment": ["transcribe"],
    "score": ["segment"],
    "select": ["score"],
    "render": ["select"],
    "copywrite": ["select"],
    "schedule": ["copywrite"],
    "publish": ["render", "schedule"],
    "collect": ["publish"],
    "report": ["collect"],
    "feedback": ["report"],
}

OUTPUTS = {
    "acquire": ["source.mp4", "audio16k_raw.wav"],
    "transcribe": ["transcript.json"],
    "turkish": ["quality_report.json"],
    "segment": ["segments.json"],
    "score": ["scores.json"],
    "select": ["clips.json"],
    "render": ["render.json"],
    "copywrite": ["posts.json"],
    "schedule": ["schedule.json"],
    "publish": ["publish.json"],
    "collect": ["metrics.json"],
    "report": ["report/report.html"],
    "feedback": ["feedback.json"],
}


@dataclass
class Manifest:
    path: Path
    data: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if path.exists():
            try:
                return cls(path, read_json(path))
            except Exception:
                pass
        return cls(path, {"schema_version": "1.0.0", "stages": {}})

    def save(self) -> None:
        write_json(self.path, self.data)

    def stage(self, name: str) -> dict:
        return self.data.setdefault("stages", {}).setdefault(name, {})

    def fingerprint_of(self, name: str) -> str:
        return self.stage(name).get("fingerprint", "")


def config_hash(config: dict, keys: list[str]) -> str:
    subset = {k: config.get(k) for k in keys}
    return hashlib.sha256(
        json.dumps(subset, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def stage_fingerprint(name: str, config: dict, manifest: Manifest) -> str:
    parts = [name, "v1", config_hash(config, CONFIG_KEYS.get(name, []))]
    parts += [manifest.fingerprint_of(d) for d in DEPENDS.get(name, [])]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def outputs_present(base: Path, name: str) -> bool:
    return all((base / rel).exists() for rel in OUTPUTS.get(name, []))


def _load_config(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _video_id(url: str) -> str:
    import re

    match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{6,})", url)
    return match.group(1) if match else hashlib.sha1(url.encode()).hexdigest()[:11]


def _min_success_ratio(config: dict) -> float:
    """How much of a stage may fall back before the run is not worth trusting."""
    return float(config.get("runtime", {}).get("min_success_ratio", 0.6))


def run_stage(name: str, base: Path, url: str, config: dict) -> None:
    """Dispatch one stage. Imports are local so a stage's dependencies are
    only loaded when that stage actually runs."""
    if name == "acquire":
        from yvc.stages.s01_acquire import acquire

        acquire(url, base, config)

    elif name == "transcribe":
        from yvc.stages.s02_transcribe import TranscribeConfig, transcribe

        whisper = config.get("whisper", {})
        transcribe(
            base / "audio16k_raw.wav", base,
            TranscribeConfig(
                model=whisper.get("model", "small"),
                compute_type=whisper.get("compute_type", "int8"),
                cpu_threads=whisper.get("cpu_threads", "auto"),
                beam_size=whisper.get("beam_size", 1),
            ),
        )

    elif name == "turkish":
        from yvc.stages.s03_turkish import analyze

        analyze(base / "transcript.json", base / "quality_report.json",
                video_id=base.name, yt_dlp=config["source"].get("yt_dlp_path"))

    elif name == "segment":
        from yvc.llm.claude_cli import ClaudeCLI
        from yvc.stages.s05_segment import segment_transcript

        cfg = config.get("segment", {})
        segment_transcript(
            base / "transcript.json", base / "segments.json",
            llm=ClaudeCLI.from_config(config.get("llm")),
            min_success_ratio=_min_success_ratio(config),
            window_s=cfg.get("window_s", 480), overlap_s=cfg.get("overlap_s", 90),
            min_segment_s=cfg.get("min_segment_s", 25),
            max_segment_s=cfg.get("max_segment_s", 300),
        )

    elif name == "score":
        from yvc.llm.claude_cli import ClaudeCLI
        from yvc.stages.s06_score import score_segments

        priors = _priors_if_enabled(config, announce="score")
        score_segments(base / "segments.json", base / "audio16k_raw.wav",
                       base / "scores.json",
                       llm=ClaudeCLI.from_config(config.get("llm")),
                       min_success_ratio=_min_success_ratio(config),
                       priors=priors)

    elif name == "select":
        from yvc.stages.s07_select import select

        cfg = config.get("select", {})
        select(
            base / "scores.json", base / "clips.json",
            segments_path=base / "segments.json",
            transcript_path=base / "transcript.json",
            vertical=cfg.get("vertical"), horizontal=cfg.get("horizontal"),
            threshold=config.get("score", {}).get("threshold", 55),
            # Selection needs the priors too, but for the opposite reason
            # scoring does: to know which hook types are being exploited
            # so it can reserve slots for the ones that are not.
            priors=_priors_if_enabled(config),
            exploration_ratio=config.get("feedback", {}).get(
                "exploration_ratio", 0.20),
            # Opening-style tagging. Assigned here so clips.json carries it
            # and render is a pure consumer, the same shape as hook_type.
            render_variant={**config.get("render_variant", {}),
                            "seed": base.name},
        )

    elif name == "render":
        from yvc.stages.s08_render import render_all

        render_all(base, min_success_ratio=_min_success_ratio(config))

    elif name == "copywrite":
        from yvc.llm.claude_cli import ClaudeCLI
        from yvc.stages.s09_copywrite import write_copy

        write_copy(
            base / "clips.json", base / "posts.json",
            llm=ClaudeCLI.from_config(config.get("llm")),
            min_success_ratio=_min_success_ratio(config),
            routing_by_aspect=config.get("publish", {}).get("routing_by_aspect"),
            bilingual=config.get("copy", {}).get("bilingual", True),
        )

    elif name in {"schedule", "publish", "collect", "report", "feedback"}:
        from yvc.stages.s10_deliver import run_delivery_stage

        run_delivery_stage(name, base, config)

    else:
        raise ValueError(f"unknown stage {name!r}")


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__)
        return 2

    command = argv.pop(0)
    if command == "doctor":
        return doctor()
    if command == "scorecard":
        return scorecard(argv)
    if command != "run":
        print(f"unknown command {command!r}")
        return 2
    if not argv:
        print("usage: yvc run <url> [--only S] [--from S] [--force S]")
        return 2

    url = argv.pop(0)
    only: list[str] = []
    start_from: str | None = None
    forced: set[str] = set()
    config_path = Path("config/config.yaml")

    while argv:
        flag = argv.pop(0)
        if flag == "--only":
            only = argv.pop(0).split(",")
        elif flag == "--from":
            start_from = argv.pop(0)
        elif flag == "--force":
            forced.update(argv.pop(0).split(","))
        elif flag == "--config":
            config_path = Path(argv.pop(0))
        else:
            print(f"unknown flag {flag!r}")
            return 2

    config = _load_config(config_path)
    video_id = _video_id(url)
    base = Path("work") / video_id
    base.mkdir(parents=True, exist_ok=True)

    manifest = Manifest.load(base / "manifest.json")
    manifest.data.update({"video_id": video_id, "source_url": url})

    plan = STAGES
    if start_from:
        plan = STAGES[STAGES.index(start_from):]
    if only:
        plan = [s for s in STAGES if s in only]

    print(f"yvc: {video_id}  stages: {', '.join(plan)}\n")

    for name in plan:
        expected = stage_fingerprint(name, config, manifest)
        record = manifest.stage(name)

        if (
            name not in forced
            and record.get("status") == "ok"
            and record.get("fingerprint") == expected
            and outputs_present(base, name)
        ):
            print(f"[{name}] skipped (up to date)")
            continue

        print(f"[{name}] running ...")
        started = time.time()
        try:
            run_stage(name, base, url, config)
        except Exception as exc:
            elapsed = round(time.time() - started, 1)
            record.update({
                "status": "failed", "fingerprint": expected,
                "duration_s": elapsed, "error": f"{type(exc).__name__}: {exc}",
            })
            manifest.save()
            print(f"[{name}] FAILED after {elapsed}s: {exc}")
            return 1

        elapsed = round(time.time() - started, 1)
        record.update({
            "status": "ok", "fingerprint": expected, "duration_s": elapsed,
            "outputs": OUTPUTS.get(name, []),
        })
        # A stage that failed and then succeeded is not a stage that
        # failed. Leaving the old message beside status "ok" makes the
        # manifest read as a contradiction weeks later.
        record.pop("error", None)
        manifest.save()
        print(f"[{name}] done in {elapsed}s\n")

    total = sum(
        s.get("duration_s", 0) for s in manifest.data.get("stages", {}).values()
    )
    print(f"yvc: complete. cumulative stage time {total / 60:.1f} min")
    print(f"artifacts: {base}")
    return 0


def scorecard(argv: list[str]) -> int:
    """Show why one segment scored what it did.

    The rubric is recorded in scores.json but only as nested JSON, which
    answers "is it written down" rather than "why did this win". This
    renders it as something a person can read -- and check, since the
    evidence quote is meant to be verified against the clip's audio.
    """
    from yvc.report.scorecard import show

    video_id = argv[0] if argv else None
    segment_id = argv[1] if len(argv) > 1 else None

    if not video_id:
        runs = sorted(Path("work").glob("*/scores.json"))
        if not runs:
            print("usage: yvc scorecard <video_id> [segment_id]")
            return 2
        video_id = runs[-1].parent.name
        print(f"(no video given, using {video_id})\n")

    config_path = Path("config/config.yaml")
    threshold = None
    if config_path.exists():
        threshold = _load_config(config_path).get("score", {}).get("threshold")

    print(show(Path("work") / video_id, segment_id, threshold=threshold))
    return 0


def doctor() -> int:
    """Environment checks, run before anything expensive."""
    ok = True

    print("== binaries ==")
    # The vendored binaries are what the stages themselves search first,
    # so doctor has to search the same path or it reports on a different
    # machine than the one the pipeline runs on.
    search = str(Path("tools/bin").resolve()) + os.pathsep + os.environ.get("PATH", "")
    for tool in ("ffmpeg", "ffprobe", "yt-dlp", "deno"):
        found = shutil.which(tool, path=search)
        print(f"  {tool:10s} {'OK ' + found if found else 'MISSING'}")
        ok &= bool(found)
    if not shutil.which("deno", path=search):
        print("    deno solves YouTube's n-signature challenge; without it")
        print("    only 360p formats are offered, with no error.")
    if not shutil.which("ffmpeg", path=search):
        print("    ffmpeg also muxes yt-dlp's separate video and audio")
        print("    streams; without it a 1080p request lands as 360p.")

    print("\n== ffmpeg features ==")
    import subprocess

    from yvc.bootstrap import child_env

    try:
        build = subprocess.run(
            ["ffmpeg", "-hide_banner", "-buildconf"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=child_env(), timeout=60,
        ).stdout
        for feature in ("libass", "fontconfig", "libfreetype", "libx264"):
            present = f"--enable-{feature}" in build
            print(f"  {feature:14s} {'OK' if present else 'MISSING'}")
            ok &= present
    except Exception as exc:
        print(f"  probe failed: {exc}")
        ok = False

    print("\n== python packages ==")
    for module in ("faster_whisper", "ctranslate2", "cv2", "numpy", "pydantic", "yaml"):
        try:
            __import__(module)
            print(f"  {module:16s} OK")
        except Exception as exc:
            print(f"  {module:16s} FAILED: {type(exc).__name__}")
            ok = False

    print("\n== claude CLI ==")
    try:
        from yvc.llm.claude_cli import ClaudeCLI

        health = ClaudeCLI().health()
        print(f"  {'OK' if health.get('ok') else 'FAILED'}  {health.get('version', '')}")
        print(f"  invocation: {health.get('invocation')}")
        ok &= bool(health.get("ok"))
    except Exception as exc:
        print(f"  FAILED: {exc}")
        ok = False

    print("\n== assets ==")
    for rel in (
        "assets/models/face_detection_yunet_2023mar.onnx",
        "config/brand.json",
        "config/config.yaml",
    ):
        present = Path(rel).exists()
        print(f"  {rel:52s} {'OK' if present else 'MISSING'}")
        ok &= present

    print("\n== fonts (Turkish glyph coverage) ==")
    try:
        from fontTools.ttLib import TTFont

        from yvc.render.fonts import resolve_font

        brand = read_json("config/brand.json")
        needed = "çÇğĞıİöÖşŞüÜ"
        for key in ("display", "body"):
            path = resolve_font(brand["fonts"][key])
            font = TTFont(str(path))
            cmap: set[int] = set()
            for table in font["cmap"].tables:
                cmap |= set(table.cmap.keys())
            missing = [c for c in needed if ord(c) not in cmap]
            family = font["name"].getDebugName(1)
            declared = brand["fonts"][f"{key}_family"]
            match = family == declared
            print(
                f"  {path.name:16s} family={family!r} "
                f"{'OK' if not missing else 'MISSING ' + ''.join(missing)}"
                f" {'' if match else f'!! declared as {declared!r}'}"
            )
            ok &= not missing and match
    except Exception as exc:
        print(f"  font check failed: {exc}")
        ok = False

    print("\n== disk ==")
    usage = shutil.disk_usage(".")
    free_gb = usage.free / 1e9
    print(f"  free {free_gb:.1f} GB  {'OK' if free_gb > 5 else 'LOW'}")
    ok &= free_gb > 5

    print("\n" + ("doctor: all checks passed" if ok else "doctor: PROBLEMS FOUND"))
    return 0 if ok else 1


app = main  # console-script entry point

if __name__ == "__main__":
    raise SystemExit(main())
