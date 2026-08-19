"""Populate a local wheelhouse using curl, then install offline.

Why this exists
---------------
This machine sits behind a Fortinet FortiGate firewall that performs TLS
inspection. The interception is *selective by client*: curl (Schannel +
HTTP/2) receives the genuine pypi.org certificate and validates cleanly,
while OpenSSL-based clients -- which includes pip, uv and Python's ssl
module -- are served a certificate issued by

    CN=FGT60FTK2209J2FQ, O=Fortinet, OU=Certificate Authority

whose root is not present in any Windows or certifi trust store. pip
therefore cannot reach PyPI at all, and ``--trusted-host`` does not help
because the index response never arrives.

The workaround: use curl (the one transport that works) to download
wheels into a local directory, then run pip fully offline against it via
``--no-index --find-links``.

Dependency resolution is delegated to pip rather than reimplemented here.
We install, read which distribution pip says is missing, fetch it, and
retry. pip stays the authority on version solving; this module is only a
download shim.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

PYPI_JSON = "https://pypi.org/pypi/{name}/json"
CURL_ARGS = ["--fail", "--silent", "--show-error", "--location", "--max-time", "120"]

# Wheel tags this interpreter accepts. Ordered by preference: a native
# cp312 build beats abi3, which beats a pure-Python wheel.
TAG_PATTERNS = [
    re.compile(r"-cp312-cp312-win_amd64\.whl$"),
    re.compile(r"-cp312-abi3-win_amd64\.whl$"),
    re.compile(r"-(?:cp3[0-9]+-)?abi3-win_amd64\.whl$"),
    re.compile(r"-py3-none-any\.whl$"),
    re.compile(r"-py2\.py3-none-any\.whl$"),
]

# pip phrases the failure differently depending on whether the requirement
# came from the command line or from another package's metadata.
# Captures "requirement pydantic-core==2.46.4", where only that exact
# version will do. Checked before the loose patterns.
EXACT_PIN = re.compile(
    r"satisfies the requirement ([A-Za-z0-9._-]+)==([A-Za-z0-9._-]+)"
)

MISSING_PATTERNS = [
    re.compile(r"No matching distribution found for ([A-Za-z0-9._-]+)"),
    re.compile(r"Could not find a version that satisfies the requirement ([A-Za-z0-9._-]+)"),
    # When a dependency is absent from the wheelhouse entirely, pip reports a
    # resolution conflict naming it rather than a missing distribution.
    re.compile(r"depends on ([A-Za-z0-9._-]+)(?:[<>=!~ ]|$)"),
]


def curl_json(url: str) -> dict | None:
    proc = subprocess.run(
        ["curl", *CURL_ARGS, url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def pick_wheels(meta: dict, versions_wanted: int) -> list[dict]:
    """Return download entries for the newest N versions that have a usable wheel.

    One version per distribution by default. Offering pip several versions
    seems helpful but backfires: pip backtracks across them and reports an
    unsolvable conflict that hides the real problem (a dependency missing
    from the wheelhouse entirely). With a single version, any failure names
    the package that is actually absent, which is what the retry loop needs.
    """
    releases: dict[str, list[dict]] = meta.get("releases", {})

    def sort_key(v: str) -> tuple:
        # Newest first, with a lexical tiebreak for non-numeric segments.
        parts = []
        for chunk in re.split(r"[._-]", v):
            parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
        return tuple(parts)

    # Exclude pre-releases. pip ignores them unless --pre is passed, so a
    # release candidate in the wheelhouse satisfies nothing and just makes
    # the resolver report a confusing "no matching distribution".
    stable = [
        v for v in releases
        if not re.search(r"(rc|a|b|dev|alpha|beta)\d*$", v, re.IGNORECASE)
    ]
    ordered = sorted(stable or releases.keys(), key=sort_key, reverse=True)

    chosen: list[dict] = []
    for version in ordered:
        files = releases[version]
        # Skip yanked releases; pip would refuse them anyway.
        candidates = [f for f in files if not f.get("yanked")]
        for pattern in TAG_PATTERNS:
            match = next(
                (f for f in candidates if pattern.search(f.get("filename", ""))), None
            )
            if match:
                chosen.append(match)
                break
        if len(chosen) >= versions_wanted:
            break
    return chosen


def fetch_exact(name: str, version: str, dest_dir: Path) -> bool:
    """Fetch one specific version.

    Needed because some packages pin a companion exactly (pydantic pins
    pydantic-core==X). Supplying only the newest version of that companion
    satisfies nothing, so the resolver has to be handed the exact build.
    """
    meta = curl_json(PYPI_JSON.format(name=name))
    if meta is None:
        print(f"    ! no PyPI metadata for {name!r}")
        return False
    files = [f for f in meta.get("releases", {}).get(version, []) if not f.get("yanked")]
    for pattern in TAG_PATTERNS:
        entry = next((f for f in files if pattern.search(f.get("filename", ""))), None)
        if entry and download(entry, dest_dir):
            print(f"    + {entry['filename']} (exact pin)")
            return True
    print(f"    ! no compatible wheel for {name}=={version}")
    return False


def download(entry: dict, dest_dir: Path, attempts: int = 6) -> bool:
    """Fetch one wheel, retrying because the TLS interception is intermittent.

    The FortiGate does not intercept every connection -- it dips in and out,
    so an individual request may fail with SEC_E_UNTRUSTED_ROOT while the
    next one to the same URL succeeds. Retrying is therefore the correct
    response rather than a papering-over: there is no persistent state to
    fix, only a proportion of connections to get through.
    """
    target = dest_dir / entry["filename"]
    if target.exists() and target.stat().st_size == entry.get("size", -1):
        return True

    for attempt in range(1, attempts + 1):
        proc = subprocess.run(
            ["curl", *CURL_ARGS, entry["url"], "-o", str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            # Guard against a truncated body being treated as a good wheel.
            expected = entry.get("size")
            if expected and target.stat().st_size != expected:
                target.unlink(missing_ok=True)
                continue
            return True

        target.unlink(missing_ok=True)
        intercepted = "UNTRUSTED_ROOT" in proc.stderr or "(60)" in proc.stderr
        if attempt == attempts:
            reason = "TLS interception" if intercepted else proc.stderr.strip()[:120]
            print(f"    ! {entry['filename']}: gave up after {attempts} tries ({reason})")
        elif not intercepted:
            # A non-TLS error (404, gone) will not fix itself.
            print(f"    ! {entry['filename']}: {proc.stderr.strip()[:120]}")
            return False
        time.sleep(min(2.0 * attempt, 6.0))
    return False


def fetch(name: str, dest_dir: Path, versions: int = 1) -> bool:
    meta = curl_json(PYPI_JSON.format(name=name))
    if meta is None:
        print(f"    ! no PyPI metadata for {name!r}")
        return False
    entries = pick_wheels(meta, versions)
    if not entries:
        print(f"    ! no cp312/win_amd64-compatible wheel for {name!r}")
        return False
    ok = False
    for entry in entries:
        if download(entry, dest_dir):
            print(f"    + {entry['filename']}")
            ok = True
    return ok


def pip_install(python: str, wheel_dir: Path, targets: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            python, "-m", "pip", "install",
            "--no-index", "--find-links", str(wheel_dir),
            *targets,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def find_missing(output: str) -> str | None:
    for pattern in MISSING_PATTERNS:
        match = pattern.search(output)
        if match:
            return match.group(1)
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: wheelhouse.py <package> [<package> ...]")
        return 2

    targets = argv[1:]
    root = Path(__file__).resolve().parent.parent
    wheel_dir = root / "wheels"
    wheel_dir.mkdir(exist_ok=True)
    python = str(root / ".venv" / "Scripts" / "python.exe")

    print(f"wheelhouse: {wheel_dir}")
    print(f"targets   : {', '.join(targets)}\n")

    print("[1] seeding requested packages")
    for name in targets:
        print(f"  {name}")
        fetch(name, wheel_dir)

    print("\n[2] resolving transitive dependencies via pip")
    seen: set[str] = set()
    for attempt in range(1, 61):
        result = pip_install(python, wheel_dir, targets)
        if result.returncode == 0:
            print(f"\n[3] install succeeded on attempt {attempt}")
            print(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "")
            return 0

        combined = result.stdout + result.stderr

        pinned = EXACT_PIN.search(combined)
        if pinned:
            name, version = pinned.group(1), pinned.group(2)
            key = f"{name.lower()}=={version}"
            if key not in seen:
                seen.add(key)
                print(f"  attempt {attempt}: pip needs {name}=={version}")
                if fetch_exact(name, version, wheel_dir):
                    continue

        missing = find_missing(combined)
        if missing is None:
            print("\n[!] pip failed for a reason other than a missing wheel:\n")
            print(combined[-3000:])
            return 1

        key = missing.lower()
        if key in seen:
            # Already supplied this one; fetching again cannot help, so the
            # constraint is genuinely unsatisfiable from available wheels.
            print(f"\n[!] '{missing}' was fetched but still does not satisfy pip.")
            print("    Likely an unsatisfiable version pin. Last pip output:\n")
            print(combined[-3000:])
            return 1
        seen.add(key)
        print(f"  attempt {attempt}: pip needs {missing!r}")
        if not fetch(missing, wheel_dir):
            print(f"\n[!] could not obtain {missing!r}; aborting.")
            return 1

    print("\n[!] exceeded attempt limit")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
