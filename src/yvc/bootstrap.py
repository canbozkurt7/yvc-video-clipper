"""Force UTF-8 at every boundary. Import this before anything else.

The Windows console on this machine defaults to code page 1252. A bare
``print()`` of Turkish text raises UnicodeEncodeError on 'ş' -- this is
not hypothetical, it crashed during development. Because the pipeline
handles Turkish text end to end (transcript, captions, social copy), a
single unguarded boundary is enough to kill a two-hour run at the last
step.

There are five boundaries, and all five are handled here:

    1. This process's stdout/stderr
    2. Child processes (claude, ffmpeg, yt-dlp)
    3. File reads and writes
    4. JSON serialization
    5. The Windows console code page itself

``yvc.io`` provides the file helpers; this module handles the rest and is
imported for its side effects at the top of the CLI.
"""

from __future__ import annotations

import os
import sys


def _reconfigure_streams() -> None:
    """Make stdout/stderr UTF-8 tolerant, including when piped."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # Already wrapped, or not a text stream.
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A detached or closed stream is not worth failing startup over.
            pass


def _set_console_codepage() -> None:
    """Best-effort: switch the Windows console to UTF-8 (65001).

    This is what stops *child* process output (ffmpeg progress lines with
    Turkish filenames) from arriving mangled. It is deliberately
    best-effort: it fails harmlessly when there is no console attached,
    such as under Task Scheduler.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass


def _set_env_defaults() -> None:
    """Seed the environment children will inherit."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for subprocess calls.

    Always pass this to ``subprocess.run(env=...)`` together with
    ``encoding="utf-8", errors="replace"``. Relying on the inherited
    environment is what lets cp1252 back in through the side door.
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def init() -> None:
    """Idempotent; safe to call more than once."""
    _set_env_defaults()
    _reconfigure_streams()
    _set_console_codepage()


init()
