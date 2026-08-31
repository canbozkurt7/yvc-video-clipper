"""Which yt-dlp the run actually invokes.

The vendored binary is named per platform -- ``tools/bootstrap.py`` writes
``yt-dlp.exe`` on Windows and ``yt-dlp`` everywhere else -- and the
resolver used to look only for the ``.exe``. On macOS the vendored copy
was therefore invisible, and the bare ``yt-dlp`` it fell back to is
exactly what is missing on a machine whose only copy is the vendored one:
the stage failed with a FileNotFoundError naming a binary that was
sitting in tools/bin.

Resolution goes through the same search path ``doctor`` reports on, so
"doctor says OK" and "the run found it" cannot disagree.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yvc.stages.s01_acquire import _yt_dlp_path


@pytest.fixture
def vendored(tmp_path, monkeypatch):
    """A repo-shaped cwd with an empty PATH, so only tools/bin can match."""
    (tmp_path / "tools" / "bin").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "")
    return tmp_path / "tools" / "bin"


def _vendor(bin_dir, name):
    exe = bin_dir / name
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return exe


@pytest.mark.skipif(os.name != "nt", reason="PATHEXT resolution is Windows-only")
def test_the_windows_vendored_exe_is_found(vendored):
    """Compared with samefile rather than by string: shutil.which returns
    the extension in PATHEXT's casing (yt-dlp.EXE), and what matters is
    which file was resolved, not how it was spelled."""
    exe = _vendor(vendored, "yt-dlp.exe")
    assert Path(_yt_dlp_path("")).samefile(exe)


@pytest.mark.skipif(os.name == "nt", reason="Windows needs the .exe extension")
def test_the_posix_vendored_binary_is_found(vendored):
    """The regression: this name was never searched for."""
    _vendor(vendored, "yt-dlp")
    assert Path(_yt_dlp_path("")).samefile(vendored / "yt-dlp")


def test_an_explicit_config_path_wins_over_the_vendored_copy(vendored, tmp_path):
    _vendor(vendored, "yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    pinned = tmp_path / "pinned-yt-dlp"
    pinned.write_text("#!/bin/sh\n")

    assert _yt_dlp_path(str(pinned)) == str(pinned)


def test_a_configured_path_that_does_not_exist_falls_back_to_the_search(vendored):
    """Config pins a binary that has since moved. Searching beats failing
    on a stale path, and the vendored copy is the whole point of tools/bin."""
    name = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
    _vendor(vendored, name)

    assert Path(_yt_dlp_path("D:/gone/yt-dlp.exe")).samefile(vendored / name)


def test_nothing_anywhere_still_yields_a_runnable_name(vendored):
    """No vendored copy and nothing on PATH. Returning the bare name lets
    subprocess raise a FileNotFoundError that names yt-dlp, which is a
    better diagnosis than a None propagating into the command list."""
    assert _yt_dlp_path("") == "yt-dlp"
