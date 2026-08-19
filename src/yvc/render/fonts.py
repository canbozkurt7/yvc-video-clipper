"""Locate the brand font without redistributing it.

``config/brand.json`` names Segoe UI Black, which is glyph-verified for
Turkish (ç ğ ı İ ö ş ü) and present on every Windows machine. It is also
proprietary: Microsoft licenses it for use *on* Windows, not for shipping
inside a public repository. Committing the .ttf would make the repo
undistributable, and dropping the font would make libass silently
substitute a fallback that lacks ğ/ş/ı and render tofu boxes into a
published clip -- a failure that only shows up after the post is live.

So the file is not committed and is resolved at render time: a repo-local
copy wins if someone has supplied one, otherwise the system font
directory. Retargeting to a free font is a two-line edit in brand.json
plus a file in assets/fonts/.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Searched in order. The repo-local directory comes first so a project
#: can pin an exact file rather than inherit whatever the OS installed.
def _search_path() -> list[Path]:
    candidates = [Path("assets/fonts")]
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        candidates.append(Path(windir) / "Fonts")
        # Per-user installs, which is where a font dropped in by
        # double-clicking it lands on Windows 10+ without admin rights.
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    candidates += [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".local/share/fonts",
        Path.home() / "Library/Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
    ]
    return candidates


class FontNotFound(FileNotFoundError):
    """Raised with the full search path, because 'font missing' is
    otherwise diagnosed from a tofu box in a rendered video."""


def resolve_font(filename: str) -> Path:
    """Return an existing path to ``filename``, or explain where we looked."""
    searched: list[str] = []
    for directory in _search_path():
        candidate = directory / filename
        searched.append(str(candidate))
        if candidate.is_file():
            return candidate
        # Linux font dirs nest by foundry; a shallow walk is cheap enough
        # and saves the user from guessing the exact subdirectory.
        if directory.is_dir() and directory.name == "fonts":
            for nested in directory.rglob(filename):
                return nested
    raise FontNotFound(
        f"brand font {filename!r} not found. Looked in:\n  "
        + "\n  ".join(searched)
        + "\n\nOn Windows this font ships with the OS and should have been "
        "found automatically.\nOn other systems, put a Turkish-capable TTF "
        "in assets/fonts/ and point config/brand.json at it "
        "(fonts.display and fonts.display_family must both match)."
    )
