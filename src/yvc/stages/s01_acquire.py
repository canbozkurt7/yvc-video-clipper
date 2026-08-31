"""Download the source video and extract ASR audio.

Three lessons from this video are encoded here as defaults:

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

* **yt-dlp needs to be told where ffmpeg is.** The 1080p video and the
  audio arrive as separate streams and have to be muxed. When ffmpeg is
  not on the child PATH -- a winget install puts it somewhere no shell
  ever sees -- the mux fails, yt-dlp falls back down the format list to
  a single-file `best`, and 360p lands on disk under the right filename.
  The failure is silent by construction: every artifact exists.

That last one is why height is now a gate rather than a warning. A
source below ``min_height`` is refused and set aside, because a 9:16
crop out of 360p cannot be published and everything downstream of it --
an hour of transcription, two LLM stages, the render -- is wasted work
spent on pixels that were never there.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from yvc.bootstrap import child_env
from yvc.io import write_json

# Ordered by observed reliability for 1080p on this network.
PLAYER_CLIENTS = ["web_embedded", "default", "tv_simply", "web_safari", "android"]

# Below this the vertical crop has fewer pixels than it needs to fill a
# 1080x1920 frame, so the run is refused rather than continued.
DEFAULT_MIN_HEIGHT = 720


def _ffmpeg_dir() -> str | None:
    """Directory holding ffmpeg, for yt-dlp's ``--ffmpeg-location``.

    yt-dlp resolves ffmpeg itself and needs it to mux the separate video
    and audio streams a 1080p download arrives as. Leaving it to chance
    is what produced a 360p source here.
    """
    import shutil

    tools = Path("tools/bin").resolve()
    path = str(tools) + os.pathsep + os.environ.get("PATH", "")
    found = shutil.which("ffmpeg", path=path)
    return str(Path(found).parent) if found else None


def _yt_dlp_path(configured: str) -> str:
    """Resolve the yt-dlp executable, honouring an explicit config path.

    Searched the same way ``doctor`` searches, and for the same reason it
    gives: a run that resolves its binaries differently from the doctor
    that cleared it is reporting on a different machine than the one it
    runs on.

    Letting ``shutil.which`` pick the filename is also what makes this
    work off Windows. ``tools/bootstrap.py`` writes ``yt-dlp.exe`` there
    and ``yt-dlp`` everywhere else, so hardcoding the ``.exe`` meant the
    vendored binary was invisible on macOS -- and the fallback to a bare
    ``yt-dlp`` on PATH then failed on a machine whose only copy was the
    vendored one.
    """
    import shutil

    tools = Path("tools/bin").resolve()
    path = str(tools) + os.pathsep + os.environ.get("PATH", "")

    # The configured value first, then the plain name. A config that pins a
    # path which has since moved is a stale pin, not an instruction to fail:
    # searching for the stale *path* only rediscovers that it is gone, so
    # the fallback has to search for the name instead.
    for candidate in (configured, "yt-dlp"):
        if not candidate:
            continue
        if Path(candidate).exists():
            return candidate
        found = shutil.which(candidate, path=path)
        if found:
            return found
    return "yt-dlp"


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


def video_stream(path: Path) -> dict:
    """Probe once and return the video stream, its container info, and
    the height as an int -- the three things every caller here wants."""
    info = probe(path)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {"info": info, "video": video, "height": int(video.get("height") or 0)}


def acquire(url: str, base: Path, config: dict) -> dict:
    """Download to base/source.mp4 and extract base/audio16k_raw.wav."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    source = base / "source.mp4"
    audio = base / "audio16k_raw.wav"

    yt_dlp = _yt_dlp_path(config.get("source", {}).get("yt_dlp_path") or "")

    fmt = config.get("source", {}).get(
        "format",
        "299+140/bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/best",
    )
    min_height = int(
        config.get("source", {}).get("min_height") or DEFAULT_MIN_HEIGHT
    )

    if source.exists():
        height = video_stream(source)["height"]
        if height and height < min_height:
            # A file under the right name is not the same as a usable
            # source. Set it aside rather than skip the download because
            # of it -- and keep it, since it is the evidence of what went
            # wrong last time.
            rejected = base / f"source.rejected-{height}p.mp4"
            rejected.unlink(missing_ok=True)
            source.rename(rejected)
            print(
                f"[acquire] existing source.mp4 is only {height}p "
                f"(minimum {min_height}p); moved to {rejected.name}, "
                "downloading again"
            )

    used_client = None
    if not source.exists():
        ffmpeg_dir = _ffmpeg_dir()
        if ffmpeg_dir is None:
            raise RuntimeError(
                "ffmpeg not found. yt-dlp needs it to mux the separate video "
                "and audio streams of a 1080p download; without it the format "
                "list falls back to a single low-resolution file."
            )
        for client in PLAYER_CLIENTS:
            cmd = [
                yt_dlp, "--no-playlist", "--no-progress", "--newline",
                "--format", fmt, "--merge-output-format", "mp4",
                "--ffmpeg-location", ffmpeg_dir,
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

    probed = video_stream(source)
    info, video, height = probed["info"], probed["video"], probed["height"]
    duration = float(info.get("format", {}).get("duration", 0.0))

    print(
        f"[acquire] {video.get('width')}x{video.get('height')} "
        f"@ {video.get('r_frame_rate')} · {duration:.0f}s · "
        f"{int(info.get('format', {}).get('size', 0)) / 1e6:.0f} MB"
    )

    if height and height < min_height:
        # Everything downstream is expensive and none of it can add
        # pixels back, so this is where the run stops.
        raise RuntimeError(
            f"source is {height}p, below the {min_height}p minimum. YouTube "
            "serves high-resolution video and audio as separate streams: if "
            "only 360p arrived, either Deno is missing (the n-signature "
            "challenge went unsolved) or ffmpeg could not be found to mux "
            "them. Both are checked by `yvc doctor`."
        )
    if height and height < 1080:
        print(
            f"[acquire] NOTE source is {height}p; a 9:16 crop out of it is "
            "upscaled to fill 1080x1920."
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
