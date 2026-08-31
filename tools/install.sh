#!/usr/bin/env bash
#
# Set up the YVC pipeline on macOS. Idempotent: safe to re-run.
#
# The macOS sibling of tools/install.ps1. Installs the prerequisites
# (Python 3.12, ffmpeg, the claude CLI), clones or updates the repo,
# creates a virtualenv, installs the package, fetches yt-dlp and Deno,
# and runs `yvc doctor`.
#
# Everything is automated except signing in to `claude`, which is a
# browser round-trip. Run `claude` once and sign in before the first video.
#
# Deliberately shorter than install.ps1. Most of that script's length is a
# three-rung pip ladder for a TLS-intercepting corporate proxy, where
# `pip install` fails in two different ways whose fixes are not
# interchangeable. That is a property of one Windows network, not of
# macOS, so this reaches for a single honest `pip install -e .` and
# reports the failure rather than guessing at a workaround it has never
# been able to test.
#
# Usage:
#   ./tools/install.sh
#   ./tools/install.sh --dest ~/work/yvc
#   ./tools/install.sh --source /path/to/existing/checkout
#   ./tools/install.sh --no-install
#   ./tools/install.sh --skip-doctor

set -euo pipefail

REPO_URL='https://github.com/canbozkurt7/yvc-video-clipper.git'
DEST="${YVC_HOME:-$HOME/yvc-video-clipper}"
SOURCE=''
NO_INSTALL=0
SKIP_DOCTOR=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dest)        DEST="$2"; shift 2 ;;
        --source)      SOURCE="$2"; shift 2 ;;
        --repo)        REPO_URL="$2"; shift 2 ;;
        --no-install)  NO_INSTALL=1; shift ;;
        --skip-doctor) SKIP_DOCTOR=1; shift ;;
        -h|--help)     sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This script targets macOS. On Windows use tools/install.ps1." >&2
    echo "Linux is not supported: it is untested, and the font and" >&2
    echo "encoder assumptions have never been exercised there." >&2
    exit 2
fi

# Colours, matched to install.ps1's Step/Ok/Warn/Bad vocabulary so the two
# scripts read the same way in a terminal.
if [ -t 1 ]; then C='\033[36m'; G='\033[32m'; Y='\033[33m'; R='\033[31m'; Z='\033[0m'
else C=''; G=''; Y=''; R=''; Z=''; fi
step() { printf "\n${C}=== %s${Z}\n" "$1"; }
ok()   { printf "  ${G}OK    %s${Z}\n" "$1"; }
warn() { printf "  ${Y}WARN  %s${Z}\n" "$1"; }
bad()  { printf "  ${R}MISS  %s${Z}\n" "$1"; }

# A space-separated string, not an array: macOS still ships bash 3.2 as
# /bin/bash, where `${#arr[@]}` and `"${arr[*]}"` on an empty array trip
# `set -u`. A string has no such edge and this list is never structured.
MISSING=''

# --- prerequisites ------------------------------------------------------
# These three cannot ship in a git repo -- a Python runtime, an ffmpeg
# build, and an npm package -- but "cannot ship" is not the same as
# "cannot install".
step 'Prerequisites'

brew_install() {
    local label="$1" formula="$2"
    if [ "$NO_INSTALL" -eq 1 ]; then
        warn "$label missing (--no-install set, skipping)"
        return 1
    fi
    if ! command -v brew >/dev/null 2>&1; then
        warn "$label missing and Homebrew is unavailable; install it by hand"
        warn '  https://brew.sh'
        return 1
    fi
    printf "  ..    installing %s\n" "$label"
    if ! brew install "$formula"; then
        warn "$label install failed"
        return 1
    fi
    # A keg-only or freshly linked formula lands on PATH for future shells,
    # not necessarily this one.
    eval "$(brew shellenv 2>/dev/null)" || true
    return 0
}

find_py312() {
    local c v
    for c in python3.12 "$(brew --prefix 2>/dev/null)/opt/python@3.12/bin/python3.12" python3; do
        [ -n "$c" ] || continue
        command -v "$c" >/dev/null 2>&1 || continue
        v="$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
        if [ "$v" = "3.12" ]; then printf '%s' "$(command -v "$c")"; return 0; fi
    done
    return 1
}

PY="$(find_py312 || true)"
if [ -n "$PY" ]; then
    ok "Python 3.12 ($PY)"
else
    bad 'Python 3.12 not found. 3.13 will not work: the pinned CTranslate2 build has no 3.13 wheel.'
    if brew_install 'Python 3.12' 'python@3.12'; then
        PY="$(find_py312 || true)"
        [ -n "$PY" ] && ok "Python 3.12 ($PY)"
    fi
    [ -n "$PY" ] || MISSING="$MISSING python3.12"
fi

if command -v ffmpeg >/dev/null 2>&1; then
    # Presence is not enough. A build without libass renders no captions,
    # and that only surfaces as a silently caption-free clip.
    if ffmpeg -hide_banner -buildconf 2>&1 | grep -q 'libass'; then
        ok 'ffmpeg (libass present)'
    else
        bad 'ffmpeg found but built without libass; captions cannot be burned in'
        warn "  Homebrew's ffmpeg includes libass; a minimal or static build may not."
        MISSING="$MISSING ffmpeg-libass"
    fi
else
    bad 'ffmpeg not found'
    if brew_install 'ffmpeg' 'ffmpeg' && command -v ffmpeg >/dev/null 2>&1; then
        ok 'ffmpeg'
    else
        MISSING="$MISSING ffmpeg"
    fi
fi

if command -v claude >/dev/null 2>&1; then
    ok 'claude CLI'
else
    bad 'claude CLI not found (the LLM engine; no API key is used)'
    if [ "$NO_INSTALL" -eq 1 ]; then
        warn 'claude CLI missing (--no-install set, skipping)'
        MISSING="$MISSING claude"
    elif command -v npm >/dev/null 2>&1; then
        printf "  ..    installing claude CLI\n"
        if npm i -g @anthropic-ai/claude-code && command -v claude >/dev/null 2>&1; then
            ok 'claude CLI'
        else
            MISSING="$MISSING claude"
        fi
    else
        warn 'npm is unavailable; install Node then re-run (brew install node)'
        MISSING="$MISSING claude"
    fi
    # Signing in is a browser round-trip and cannot be scripted; it is the
    # one genuinely manual step in the whole setup.
    warn 'run `claude` once and sign in before the first video'
fi

case " $MISSING " in
    *' python3.12 '*) echo "Cannot continue without Python 3.12." >&2; exit 1 ;;
esac

# --- checkout ----------------------------------------------------------
step "Checkout -> $DEST"
if [ -d "$DEST/.git" ]; then
    ok 'already a git checkout; pulling'
    git -C "$DEST" pull --ff-only 2>&1 | sed 's/^/        /'
else
    FROM="${SOURCE:-$REPO_URL}"
    printf "  cloning from %s\n" "$FROM"
    git clone --quiet "$FROM" "$DEST"
    if [ -n "$SOURCE" ]; then
        # Leave the canonical remote in place so `git pull` works later even
        # though the bits came from a local path.
        git -C "$DEST" remote set-url origin "$REPO_URL"
    fi
    ok 'cloned'
fi

# --- virtualenv --------------------------------------------------------
step 'Virtualenv'
VENV="$DEST/.venv"
VPY="$VENV/bin/python"
if [ ! -x "$VPY" ]; then
    "$PY" -m venv "$VENV"
    ok 'created'
else
    ok 'already present'
fi

step 'Dependencies'
cd "$DEST"
"$VPY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
echo '  [1] pip install -e .'
if ! "$VPY" -m pip install -e . 2>&1 | sed 's/^/        /'; then
    warn 'pip install failed; see the log above'
fi

# Verify rather than trust. An earlier version of the Windows script
# reported success after every rung had failed, because --no-deps happily
# installs the package alone. The same trap exists here: pip's exit status
# travels through a pipe, so the import probe is what actually decides.
step 'Verifying'
PROBE="$("$VPY" -c '
import importlib.util
missing = [m for m in ("faster_whisper", "ctranslate2", "cv2", "numpy",
                       "pydantic", "yaml", "typer")
           if not importlib.util.find_spec(m)]
print(",".join(missing))
' 2>&1 | tr -d '[:space:]' || true)"
if [ -n "$PROBE" ]; then
    echo "Dependencies missing after install: $PROBE" >&2
    echo "The install did not work. See the log above." >&2
    exit 1
fi
ok 'all imports resolve'
YVC="$VENV/bin/yvc"
if [ ! -x "$YVC" ]; then
    echo "The 'yvc' entry point was not created at $YVC." >&2
    exit 1
fi
ok 'yvc entry point present'

step 'Binaries (yt-dlp, Deno)'
"$VPY" tools/bootstrap.py 2>&1 | sed 's/^/        /' ||     warn 'binary fetch failed; doctor will name what is missing'

# --- brand font --------------------------------------------------------
# config/brand.json names Segoe UI Black, which ships with Windows and is
# absent on macOS. render fails loudly with FontNotFound rather than
# substituting silently -- libass would otherwise pick a fallback missing
# g-breve and s-cedilla and render tofu boxes into a published clip -- but
# a loud failure ninety minutes into a run is still a wasted run, so say
# so here instead.
step 'Brand font'
FONT="$(python3 - <<'PY' 2>/dev/null || true
import json
print(json.load(open("config/brand.json", encoding="utf-8"))["fonts"]["display"])
PY
)"
if [ -n "$FONT" ] && [ ! -f "assets/fonts/$FONT" ] \
   && [ ! -f "/Library/Fonts/$FONT" ] && [ ! -f "$HOME/Library/Fonts/$FONT" ] \
   && [ ! -f "/System/Library/Fonts/$FONT" ]; then
    warn "brand font '$FONT' is not on this machine (it ships with Windows)"
    warn '  The render stage will stop with FontNotFound until you either:'
    warn "    - put a Turkish-capable .ttf in assets/fonts/ and set both"
    warn '      fonts.display and fonts.display_family in config/brand.json'
    warn '      to match it, or'
    warn '    - point those two fields at a font already installed here.'
    warn '  Both fields must change together: display is the filename,'
    warn "  display_family is the family name inside the font's name table,"
    warn '  and a mismatch is what makes libass substitute silently.'
else
    [ -n "$FONT" ] && ok "brand font '$FONT' resolves"
fi

if [ "$SKIP_DOCTOR" -eq 0 ]; then
    step 'Doctor'
    "$YVC" doctor || true
fi

step 'Done'
printf "  Checkout: %s\n" "$DEST"
printf "  Run a video:  %s run \"<youtube-url>\"\n" "$YVC"
if [ -n "$MISSING" ]; then
    warn "Still missing:$MISSING. Install those, then re-run this script."
fi
