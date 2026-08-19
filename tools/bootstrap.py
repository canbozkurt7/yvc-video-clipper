"""Fetch the vendored binaries a fresh clone does not carry.

``tools/bin/`` holds ~110 MB of platform-specific executables. They are
freely redistributable (yt-dlp is Unlicense, Deno is MIT) but they do not
belong in git: they are large, they are per-platform, and yt-dlp in
particular goes stale within weeks as YouTube changes its player.

Deno is not optional. YouTube's n-signature challenge needs a JavaScript
runtime, and without one yt-dlp does not error -- it silently offers only
360p formats. That is the single most confusing failure this pipeline
has, so this script installs it by default.

    python tools/bootstrap.py            # both, if missing
    python tools/bootstrap.py --force    # re-download (yt-dlp goes stale)

Nothing here is required if yt-dlp and deno are already on PATH.
"""

from __future__ import annotations

import argparse
import io
import os
import platform
import shutil
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path

BIN = Path(__file__).resolve().parent / "bin"

YT_DLP = {
    ("Windows", "AMD64"): "yt-dlp.exe",
    ("Linux", "x86_64"): "yt-dlp_linux",
    ("Darwin", "arm64"): "yt-dlp_macos",
    ("Darwin", "x86_64"): "yt-dlp_macos",
}
YT_DLP_BASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"

DENO = {
    ("Windows", "AMD64"): "deno-x86_64-pc-windows-msvc.zip",
    ("Linux", "x86_64"): "deno-x86_64-unknown-linux-gnu.zip",
    ("Darwin", "arm64"): "deno-aarch64-apple-darwin.zip",
    ("Darwin", "x86_64"): "deno-x86_64-apple-darwin.zip",
}
DENO_BASE = "https://github.com/denoland/deno/releases/latest/download/"


def _key() -> tuple[str, str]:
    return platform.system(), platform.machine()


def _context() -> ssl.SSLContext | None:
    """Honour a corporate TLS bundle, the same knobs the metrics
    collectors use. Networks that intercept TLS break the default
    verification, and the fix should not be 'disable verification'."""
    bundle = (
        os.environ.get("YVC_CA_BUNDLE")
        or os.environ.get("REQUESTS_CA_BUNDLE")
        or os.environ.get("SSL_CERT_FILE")
    )
    if bundle:
        return ssl.create_default_context(cafile=bundle)
    return None


def _get(url: str) -> bytes:
    print(f"  fetching {url}")
    with urllib.request.urlopen(url, context=_context(), timeout=300) as response:
        return response.read()


def fetch_yt_dlp(force: bool) -> bool:
    name = YT_DLP.get(_key())
    if not name:
        print(f"  no yt-dlp build listed for {_key()} -- install it yourself")
        return False
    target = BIN / ("yt-dlp.exe" if name.endswith(".exe") else "yt-dlp")
    if target.exists() and not force:
        print(f"  yt-dlp already present: {target}")
        return True
    target.write_bytes(_get(YT_DLP_BASE + name))
    target.chmod(0o755)
    print(f"  installed {target}")
    return True


def fetch_deno(force: bool) -> bool:
    name = DENO.get(_key())
    if not name:
        print(f"  no Deno build listed for {_key()} -- install it yourself")
        return False
    exe = "deno.exe" if platform.system() == "Windows" else "deno"
    target = BIN / exe
    if target.exists() and not force:
        print(f"  deno already present: {target}")
        return True
    archive = zipfile.ZipFile(io.BytesIO(_get(DENO_BASE + name)))
    with archive.open(exe) as source, target.open("wb") as out:
        shutil.copyfileobj(source, out)
    target.chmod(0o755)
    print(f"  installed {target}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="re-download even if the binary is present")
    args = parser.parse_args()

    BIN.mkdir(parents=True, exist_ok=True)
    print(f"target: {BIN}")
    ok = fetch_yt_dlp(args.force)
    ok &= fetch_deno(args.force)
    print("\nNow run:  yvc doctor")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
