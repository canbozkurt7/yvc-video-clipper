"""Download the source video and extract ASR audio.

Two lessons from this video are encoded here as defaults:

* **A JS runtime is required.** YouTube's n-signature challenge must be
  solved to see high-resolution formats. Without Deno on PATH, yt-dlp
  silently offers only 360p -- it does not error, it just returns a
  shorter format list. The bundled ``tools/bin`` is prepended to PATH so
  a vendored Deno is found automatically.

* **Player clients differ in what they can actually fetch.** The format
  list and the download are separate gates: `android` served only 360p,
  `tv` hit DRM, and the default client returned HTTP 403 on format 299
  even after the challenge was solved. `web_embedded` worked. Because
  which client works changes over time, the clients are tried in order
  and the one that succeeds is recorded.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from yvc.bootstrap import child_env
from yvc.io import write_json

# Ordered by observed reliability for 1080p on this network.
PLAYER_CLIENTS = ["web_embedded", "default", "tv_simply", "web_safari", "android"]


def _run(cmd: list[str], timeout: int = 5400) -> subprocess.CompletedProcess:
    env = child_env()
    # Prepend the vendored binaries so a bundled Deno satisfies the
    # n-signature challenge without a system-wide install.
    tools = str(Path("tools/bin").resolve())
    env["PATH"] = tools + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, timeout=timeout,
    )


def probe(path: Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ], timeout=300)
    import json

    return json.loads(result.stdout or "{}")


def acquire(url: str, base: Path, config: dict) -> dict:
    """Download to base/source.mp4 and extract base/audio16k_raw.wav."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    source = base / "source.mp4"
    audio = base / "audio16k_raw.wav"

    yt_dlp = config.get("source", {}).get("yt_dlp_path") or "yt-dlp"
    if not Path(yt_dlp).exists() and Path("tools/bin/yt-dlp.exe").exists():
        yt_dlp = str(Path("tools/bin/yt-dlp.exe").resolve())

    fmt = config.get("source", {}).get(
        "format",
        "299+140/bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/best",
    )

    used_client = None
    if not source.exists():
        for client in PLAYER_CLIENTS:
            cmd = [
                yt_dlp, "--no-playlist", "--no-progress", "--newline",
                "--format", fmt, "--merge-output-format", "mp4",
                "--output", str(base / "source.%(ext)s"),
                "--write-info-json", "--continue", "--no-overwrites",
                "--retries", "10", "--fragment-retries", "10",
                "--encoding", "utf-8",
            ]
            if client != "default":
                cmd += ["--extractor-args", f"youtube:player_client={client}"]
            cmd.append(url)

            print(f"[acquire] trying player_client={client} ...")
            result = _run(cmd)
            if source.exists():
                used_client = client
                print(f"[acquire] downloaded with player_client={client}")
                break
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-1:]
            print(f"[acquire]   failed: {tail[0][:160] if tail else 'unknown'}")

        if not source.exists():
            raise RuntimeError(
                "download failed for every player client. If only low-resolution "
                "formats were offered, a JS runtime (Deno) is missing and the "
                "n-signature challenge could not be solved."
            )
    else:
        print("[acquire] source.mp4 already present; skipping download")

    info = probe(source)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    duration = float(info.get("format", {}).get("duration", 0.0))

    print(
        f"[acquire] {video.get('width')}x{video.get('height')} "
        f"@ {video.get('r_frame_rate')} · {duration:.0f}s · "
        f"{int(info.get('format', {}).get('size', 0)) / 1e6:.0f} MB"
    )

    if video.get("height", 0) and int(video["height"]) < 720:
        # Not fatal, but the vertical crop needs the pixels; say so loudly
        # rather than silently producing soft clips.
        print(
            f"[acquire] WARNING source is only {video['height']}p. "
            "Vertical 9:16 crops will be low resolution."
        )

    if not audio.exists():
        print("[acquire] extracting 16 kHz mono audio ...")
        result = _run([
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-i", str(source), "-vn", "-sn", "-dn",
            "-af", "aresample=resampler=soxr:precision=28,"
                   "pan=mono|c0=0.5*FL+0.5*FR,highpass=f=60",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(audio),
        ])
        if result.returncode != 0:
            raise RuntimeError(f"audio extraction failed: {result.stderr[-400:]}")

    payload = {
        "url": url,
        "player_client": used_client,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": video.get("r_frame_rate"),
        "duration_s": duration,
        "size_bytes": int(info.get("format", {}).get("size", 0)),
        "audio_path": str(audio),
    }
    write_json(base / "acquire.json", payload)
    return payload
