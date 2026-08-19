"""Regression tests for the cp1252 crash.

A naive print() of Turkish text raised UnicodeEncodeError on 'ş' during
development. These tests reproduce the hostile conditions (cp1252 as the
ambient encoding) and assert every I/O boundary survives.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[2] / "src")
SAMPLE = "Şimdi bordro dediğimiz şey: çğıİöşü ÇĞIİÖŞÜ — %40'ı vergiye gidiyor."


def _run_child(code: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONPATH"] = SRC
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def test_bare_print_without_bootstrap_is_the_known_hazard():
    """Documents the failure mode. If this ever stops failing, the
    environment changed and the bootstrap's necessity should be re-checked."""
    result = _run_child(
        f"import sys; sys.stdout.reconfigure(encoding='cp1252'); print({SAMPLE!r})",
        {},
    )
    assert result.returncode != 0
    assert "UnicodeEncodeError" in result.stderr


def test_bootstrap_makes_turkish_print_safe_under_cp1252():
    result = _run_child(
        "import sys; sys.stdout.reconfigure(encoding='cp1252')\n"
        "import yvc.bootstrap\n"
        f"print({SAMPLE!r})",
        {},
    )
    assert result.returncode == 0, result.stderr
    assert "bordro dediğimiz" in result.stdout


def test_file_roundtrip_preserves_turkish(tmp_path):
    sys.path.insert(0, SRC)
    from yvc.io import read_json, read_text, write_json, write_text

    target = tmp_path / "t.txt"
    write_text(target, SAMPLE)
    assert read_text(target) == SAMPLE
    assert target.read_bytes()[:3] != b"\xef\xbb\xbf", "BOM must not be written"

    jtarget = tmp_path / "t.json"
    write_json(jtarget, {"metin": SAMPLE})
    assert read_json(jtarget)["metin"] == SAMPLE
    raw = jtarget.read_text(encoding="utf-8")
    assert "ş" in raw, "ensure_ascii=False keeps artifacts human-readable"


def test_atomic_write_leaves_no_partial_file(tmp_path):
    sys.path.insert(0, SRC)
    from yvc.io import write_text

    target = tmp_path / "a.txt"
    write_text(target, SAMPLE)
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_jsonl_tolerates_truncated_final_line(tmp_path):
    sys.path.insert(0, SRC)
    from yvc.io import append_jsonl, read_jsonl

    target = tmp_path / "c.jsonl"
    append_jsonl(target, {"i": 0, "t": SAMPLE})
    append_jsonl(target, {"i": 1, "t": SAMPLE})
    with open(target, "a", encoding="utf-8") as fh:
        fh.write('{"i": 2, "t": "yarı')  # simulate a crash mid-write

    rows = read_jsonl(target)
    assert [r["i"] for r in rows] == [0, 1]
    assert rows[0]["t"] == SAMPLE


def test_child_env_forces_utf8():
    sys.path.insert(0, SRC)
    from yvc.bootstrap import child_env

    env = child_env()
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
