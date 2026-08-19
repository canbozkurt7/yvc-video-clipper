"""File and JSON helpers that hardcode UTF-8.

Bare ``open()`` is banned in this project -- on Windows it picks up the
locale encoding, which is cp1252 here. Every read and write goes through
these helpers instead. ``tests/unit/test_no_bare_open.py`` enforces it.

Writes are atomic: content goes to a temporary file in the same directory
and is then moved into place. A crash mid-write therefore cannot leave a
half-written artifact behind, which matters because the resume logic
trusts that any artifact present on disk is complete.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_text(path: str | Path) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write_text(path: str | Path, content: str, *, newline: str = "\n") -> None:
    """Atomically write text as UTF-8 without a BOM.

    ``newline`` is explicit because ASS subtitle files must use LF; libass
    tolerates CRLF but some tooling leaks the CR into the rendered line.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(content)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def read_json(path: str | Path) -> Any:
    return json.loads(read_text(path))


def write_json(path: str | Path, obj: Any, *, indent: int = 2) -> None:
    """Write JSON with ensure_ascii=False.

    Escaping non-ASCII to escape sequences would technically work, but
    it would make every artifact unreadable to a human reviewing Turkish
    transcripts and hook rationales, which is most of the debugging here.
    """
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent) + "\n")


def append_jsonl(path: str | Path, obj: Any, *, fsync: bool = False) -> None:
    """Append one JSON object as a line. Used for transcription checkpoints."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False) + "\n")
        if fsync:
            handle.flush()
            os.fsync(handle.fileno())


def read_jsonl(path: str | Path) -> list[Any]:
    """Read a JSONL file, tolerating a truncated final line.

    A crash during checkpointing can leave a partial last line. Dropping
    it is correct: the corresponding work is simply redone on resume.
    """
    path = Path(path)
    if not path.exists():
        return []
    rows: list[Any] = []
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return rows
