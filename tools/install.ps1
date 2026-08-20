<#
.SYNOPSIS
  Set up the YVC pipeline on this machine. Idempotent: safe to re-run.

.DESCRIPTION
  Installs the prerequisites (Python 3.12, ffmpeg, the claude CLI), clones
  or updates the repo, creates a virtualenv, installs the package, fetches
  yt-dlp and Deno, and runs `yvc doctor`.

  Everything is automated except signing in to `claude`, which is a browser
  round-trip. Run `claude` once and sign in before the first video.

  Pass -NoInstall to report missing prerequisites instead of installing
  them.

.EXAMPLE
  ./tools/install.ps1
  ./tools/install.ps1 -Dest D:\work\yvc
  ./tools/install.ps1 -Source \path\to\existing\checkout
  ./tools/install.ps1 -NoInstall
#>
[CmdletBinding()]
param(
    # Where the working checkout lives. This is a normal git clone you can
    # edit; it is deliberately NOT inside the plugin cache, which gets wiped
    # on plugin updates and should not accumulate 1 GB of rendered video.
    [string]$Dest = $(if ($env:YVC_HOME) { $env:YVC_HOME } else { Join-Path $HOME 'yvc-video-clipper' }),

    [string]$Repo = 'https://github.com/canbozkurt7/yvc-video-clipper.git',

    # Clone from a local checkout instead of the network. Used when the
    # plugin is already on disk, and the only path that works for a private
    # repo on a machine without git credentials.
    [string]$Source = '',

    [switch]$SkipDoctor,

    # Report missing prerequisites instead of installing them. For machines
    # where Python or ffmpeg are managed by something else and winget would
    # install a second, conflicting copy.
    [switch]$NoInstall
)

$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  OK    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "  MISS  $m" -ForegroundColor Red }

# --- prerequisites ------------------------------------------------------
# These three cannot ship in a git repo -- a Python runtime, an ffmpeg
# build, and an npm package -- but "cannot ship" is not the same as
# "cannot install". Earlier this step only printed the winget command and
# left the operator to paste it, which turned a one-command setup into a
# checklist. Now it runs them, unless -NoInstall says otherwise.
Step 'Prerequisites'
$missing = @()

$winget = [bool](Get-Command winget -ErrorAction SilentlyContinue)

function Try-Install($label, $exe, $argList) {
    if ($NoInstall) { Warn "$label missing (-NoInstall set, skipping)"; return $false }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
        Warn "$label missing and $exe is unavailable; install it by hand"
        return $false
    }
    Write-Host "  ..    installing $label" -ForegroundColor DarkGray
    & $exe @argList 2>&1 | Out-String | Write-Verbose
    if ($LASTEXITCODE -ne 0) { Warn "$label install returned $LASTEXITCODE"; return $false }
    # winget puts new shims on PATH for *future* shells, not this one.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
    return $true
}

$py = $null
foreach ($c in @('py -3.12', 'python3.12', 'python')) {
    $exe, $arg = $c -split ' ', 2
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $v = & $exe $arg -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
    } catch { continue }
    if ($v -eq '3.12') { $py = $c; Ok "Python 3.12 ($c)"; break }
}
if (-not $py) {
    Bad 'Python 3.12 not found. 3.13 will not work: the pinned CTranslate2 build has no 3.13 wheel.'
    if (Try-Install 'Python 3.12' 'winget' @(
            'install', '--id', 'Python.Python.3.12', '-e',
            '--accept-package-agreements', '--accept-source-agreements')) {
        foreach ($c in @('py -3.12', 'python3.12', 'python')) {
            $exe, $arg = $c -split ' ', 2
            if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
            try { $v = & $exe $arg -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null }
            catch { continue }
            if ($v -eq '3.12') { $py = $c; Ok "Python 3.12 ($c)"; break }
        }
    }
    if (-not $py) { $missing += 'python3.12' }
}

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    # Presence is not enough. A build without libass renders no captions,
    # and that only surfaces as a silently caption-free clip.
    if ((& ffmpeg -hide_banner -buildconf 2>&1 | Out-String) -match 'libass') { Ok 'ffmpeg (libass present)' }
    else { Bad 'ffmpeg found but built without libass; captions cannot be burned in'; $missing += 'ffmpeg-libass' }
} else {
    Bad 'ffmpeg not found'
    if (Try-Install 'ffmpeg' 'winget' @(
            'install', '--id', 'Gyan.FFmpeg', '-e',
            '--accept-package-agreements', '--accept-source-agreements')) {
        if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Ok 'ffmpeg' }
        else { Warn 'ffmpeg installed but not yet on PATH; reopen the shell'; $missing += 'ffmpeg' }
    } else { $missing += 'ffmpeg' }
}

if (Get-Command claude -ErrorAction SilentlyContinue) { Ok 'claude CLI' }
else {
    Bad 'claude CLI not found (the LLM engine; no API key is used)'
    if (Try-Install 'claude CLI' 'npm' @('i', '-g', '@anthropic-ai/claude-code')) {
        if (Get-Command claude -ErrorAction SilentlyContinue) { Ok 'claude CLI' }
        else { $missing += 'claude' }
    } else { $missing += 'claude' }
    # Signing in is a browser round-trip and cannot be scripted; it is the
    # one genuinely manual step in the whole setup.
    Warn 'run `claude` once and sign in before the first video'
}

if ($missing -contains 'python3.12') { throw "Cannot continue without Python 3.12." }

# --- checkout ----------------------------------------------------------
Step "Checkout -> $Dest"
# Windows still caps paths at 260 characters unless long paths are enabled.
# pip unpacks deeply nested fixture files, so a long destination fails with
# WinError 206 only after downloading every wheel.
$longPaths = $false
try {
    $longPaths = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -ErrorAction Stop).LongPathsEnabled -eq 1
} catch { }
if ($Dest.Length -gt 90 -and -not $longPaths) {
    Warn "Destination path is $($Dest.Length) characters. Windows MAX_PATH will break pip partway through."
    Warn "Use a shorter -Dest (for example C:\yvc), or enable long paths system-wide."
    throw "Refusing to install into a path this long."
}
if (Test-Path (Join-Path $Dest '.git')) {
    Ok 'already a git checkout; pulling'
    git -C $Dest pull --ff-only 2>&1 | ForEach-Object { "        $_" }
} else {
    $from = if ($Source) { $Source } else { $Repo }
    Write-Host "  cloning from $from"
    git clone --quiet $from $Dest
    if ($Source) {
        # Leave the canonical remote in place so `git pull` works later even
        # though the bits came from a local path.
        git -C $Dest remote set-url origin $Repo
    }
    Ok 'cloned'
}

# --- virtualenv --------------------------------------------------------
Step 'Virtualenv'
$venv = Join-Path $Dest '.venv'
$vpy  = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $vpy)) {
    $exe, $arg = $py -split ' ', 2
    if ($arg) { & $exe $arg -m venv $venv } else { & $exe -m venv $venv }
    Ok 'created'
} else { Ok 'already present' }

Step 'Dependencies'
Push-Location $Dest
try {
    & $vpy -m pip install --quiet --upgrade pip 2>&1 | Out-Null

    # A ladder, because "pip install" fails in two very different ways on
    # corporate networks and the fixes are not interchangeable.
    $installed = $false

    Write-Host '  [1] pip install -e .'
    & $vpy -m pip install -e . 2>&1 | ForEach-Object { "        $_" }
    if ($LASTEXITCODE -eq 0) { $installed = $true; Ok 'installed from PyPI' }

    if (-not $installed) {
        # TLS-intercepting proxies (this project was built behind a
        # FortiGate) break the chain to files.pythonhosted.org, often only
        # during build isolation. Trusting those three hosts is what the
        # original machine ended up doing; it skips certificate
        # verification for them, so it is a real tradeoff, not a no-op.
        Warn 'pip failed. Retrying with --trusted-host (skips TLS verification for PyPI hosts)'
        $hosts = @('--trusted-host', 'pypi.org', '--trusted-host', 'files.pythonhosted.org',
                   '--trusted-host', 'pypi.python.org')
        & $vpy -m pip install @hosts -e . 2>&1 | ForEach-Object { "        $_" }
        if ($LASTEXITCODE -eq 0) {
            $installed = $true
            Ok 'installed with --trusted-host'
            # Persist it so later `pip install` calls in this venv behave.
            $ini = Join-Path $venv 'pip.ini'
            if (-not (Test-Path $ini)) {
                @('[global]', 'trusted-host = pypi.org', '               files.pythonhosted.org',
                  '               pypi.python.org') | Set-Content -Path $ini -Encoding ascii
                Ok "wrote $ini so this venv keeps working"
            }
        }
    }

    if (-not $installed) {
        Warn 'Still failing. Trying the curl-based wheelhouse'
        & $vpy tools/wheelhouse.py faster-whisper opencv-python-headless numpy pydantic PyYAML rich tenacity psutil typer jinja2 structlog filelock regex fonttools pytest
        & $vpy -m pip install -e . --no-index --no-build-isolation --no-deps 2>&1 | ForEach-Object { "        $_" }
        if ($LASTEXITCODE -eq 0) { $installed = $true; Ok 'installed from the local wheelhouse' }
    }

    # Verify rather than trust. An earlier version of this script reported
    # success after every rung had failed, because --no-deps happily
    # installs the package alone and native exit codes do not stop
    # PowerShell by default.
    Step 'Verifying'
    $probe = (& $vpy -c @'
import importlib.util
missing = [m for m in ("faster_whisper", "ctranslate2", "cv2", "numpy",
                       "pydantic", "yaml", "typer")
           if not importlib.util.find_spec(m)]
print(",".join(missing))
'@ 2>&1 | Out-String).Trim()
    $yvcExe = Join-Path $venv 'Scripts\yvc.exe'
    if ($probe) { throw "Dependencies missing after install: $probe`nNone of the install strategies worked. See the log above." }
    Ok 'all imports resolve'
    if (-not (Test-Path $yvcExe)) { throw "The 'yvc' entry point was not created at $yvcExe." }
    Ok 'yvc entry point present'

    Step 'Binaries (yt-dlp, Deno)'
    & $vpy tools/bootstrap.py | ForEach-Object { "        $_" }

    if (-not $SkipDoctor) {
        Step 'Doctor'
        & $yvcExe doctor
    }
} finally { Pop-Location }

Step 'Done'
Write-Host "  Checkout: $Dest"
Write-Host "  Run a video:" -NoNewline
Write-Host "  $venv\Scripts\yvc.exe run `"<youtube-url>`"" -ForegroundColor White
if ($missing.Count) {
    Warn "Still missing: $($missing -join ', '). Install those, then re-run this script."
}
