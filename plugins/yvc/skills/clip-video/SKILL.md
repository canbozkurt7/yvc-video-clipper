---
name: clip-video
description: Turn a long-form video (YouTube URL or local file) into publishable social clips — vertical Shorts/Reels and horizontal LinkedIn/X cuts — with transcription, hook scoring, captions and per-platform copy. Use when the user gives a video URL and wants clips, Shorts, Reels, or social cuts made from it. Also handles first-time setup of the pipeline.
---

# Clip a long video into social posts

This skill drives YVC, a no-touch pipeline that takes one long video and
produces finished vertical and horizontal clips with burned-in captions,
per-platform copy, a publishing queue and a measurement report.

The pipeline is a Python CLI. Your job is to make sure it is installed,
then run it and interpret what it reports. **Do not reimplement any stage
yourself** — the value is in the scoring and selection logic that already
exists.

## Platform

Windows and macOS. The two differ only in the installer to call and the
path to the entry point, so every command below is given twice — pick the
line for the machine you are on and use it consistently.

| | Windows | macOS |
|---|---|---|
| installer | `tools\install.ps1` | `tools/install.sh` |
| entry point | `.venv\Scripts\yvc.exe` | `.venv/bin/yvc` |
| prerequisites via | winget + npm | Homebrew + npm |

Linux is not supported. Say so plainly rather than improvising: the font
and encoder assumptions have never been exercised there.

## Step 1 — find or install the checkout

The pipeline needs a real working directory, not the plugin cache: a run
writes ~1 GB of video into `work/`, and a plugin update wipes the cache.
So the checkout always lives outside it. Set `$YVC` to that directory once
and reuse it in every later step.

Look for an existing checkout in this order:

1. `$env:YVC_HOME` / `$YVC_HOME`
2. `~/yvc-video-clipper`

If one exists, that is your `$YVC`. If neither does, install — and prefer
running the installer over guessing at state, because it is idempotent:
on an existing checkout it just pulls and re-verifies.

**Where to install *from* matters, and the answer is measured rather than
assumed.** A marketplace install copies only the plugin's own directory
into `${CLAUDE_PLUGIN_ROOT}`, which is
`<config>/plugins/cache/<marketplace>/<plugin>/<version>` and holds just
`.claude-plugin/`, `commands/` and `skills/`. There is no `tools/` or
`src/` beside it, so `${CLAUDE_PLUGIN_ROOT}/../../tools/install.ps1` does
not resolve — do not try it.

But a **git-source** marketplace also leaves a full clone of the repo at
`<config>/plugins/marketplaces/<marketplace>/`, and that clone does have
`tools/` and `src/`. When it is there, install from it: the installer's
`-Source` / `--source` flag exists for exactly this, and it saves a
second network clone of the whole repo. It also works when the reviewer
has no git credentials for a private repo.

Derive the clone path from `${CLAUDE_PLUGIN_ROOT}` rather than hardcoding
`~/.claude`, since the config directory can be relocated:

```powershell
$pr    = Get-Item $env:CLAUDE_PLUGIN_ROOT
$clone = Join-Path $pr.Parent.Parent.Parent.Parent.FullName `
                   "marketplaces\$($pr.Parent.Parent.Name)"
$YVC   = if ($env:YVC_HOME) { $env:YVC_HOME } else { Join-Path $HOME 'yvc-video-clipper' }

if (Test-Path (Join-Path $clone 'tools\install.ps1')) {
    & (Join-Path $clone 'tools\install.ps1') -Dest $YVC -Source $clone
} else {
    git clone https://github.com/canbozkurt7/yvc-video-clipper.git $YVC
    & (Join-Path $YVC 'tools\install.ps1') -Dest $YVC
}
```

```bash
PR="$CLAUDE_PLUGIN_ROOT"
CLONE="$(dirname "$(dirname "$(dirname "$(dirname "$PR")")")")/marketplaces/$(basename "$(dirname "$(dirname "$PR")")")"
YVC="${YVC_HOME:-$HOME/yvc-video-clipper}"

if [ -f "$CLONE/tools/install.sh" ]; then
    bash "$CLONE/tools/install.sh" --dest "$YVC" --source "$CLONE"
else
    git clone https://github.com/canbozkurt7/yvc-video-clipper.git "$YVC"
    bash "$YVC/tools/install.sh" --dest "$YVC"
fi
```

The installer handles the three prerequisites that cannot ship in a git
repo — Python 3.12 (3.13 has no wheel for the pinned CTranslate2), ffmpeg
built with libass, and the `claude` CLI. If it could not install one (no
winget, no Homebrew, a locked-down machine), it says so per item rather
than failing obscurely. Report what it said verbatim.

One thing is left to the user, and it blocks the run: **signing in to
`claude`**. It is a browser round-trip. If `yvc doctor` reports the CLI
as failing, **stop and tell the user to run `claude` once and sign in**;
do not try to work around it, and do not look for an API key — there
isn't one, the CLI is the LLM engine.

**On macOS, expect one more thing to be missing.** `config/brand.json`
names Segoe UI Black, which ships with Windows and is not present on a
Mac. The render stage stops with `FontNotFound` rather than substituting
silently, because libass would otherwise pick a fallback lacking ğ/ş/ı
and burn tofu boxes into a clip nobody inspects until after it is
published. `install.sh` warns about this up front. The fix is to put a
Turkish-capable `.ttf` in `assets/fonts/` and update **both**
`fonts.display` (the filename) and `fonts.display_family` (the family
name inside the font's `name` table) in `config/brand.json` — they must
change together, since a mismatch is precisely what makes libass
substitute without complaining.

## Step 2 — run doctor before a long run

```powershell
& "$YVC\.venv\Scripts\yvc.exe" doctor
```

```bash
"$YVC/.venv/bin/yvc" doctor
```

Doctor probes ffmpeg's filters, the `claude` CLI's invocation form, font
glyph coverage for `ç ğ ı İ ö ş ü`, and free disk. It exists so setup
problems surface in seconds instead of ninety minutes into transcription.
If it fails, fix what it names before running anything else.

Run it from inside `$YVC`: it resolves `tools/bin`, `config/` and
`assets/` relative to the working directory, as the pipeline itself does.

## Step 3 — run the pipeline

```powershell
& "$YVC\.venv\Scripts\yvc.exe" run "<url>"
```

```bash
"$YVC/.venv/bin/yvc" run "<url>"
```

Set expectations **before** starting, because this is slow and the user
should not think it has hung:

- **~1h50m** wall-clock for a 60-minute source on a 4-core laptop,
  measured; transcription is 52 min of it.
- Transcription alone is 60–90 minutes and dominates everything else.
- Output is buffered. Silence is not failure. Do not kill the run to
  "check on it" — resume works, but a killed run wastes real time.
- Run it in the background and check the stage artifacts under
  `work/<video-id>/` rather than watching stdout.

Useful flags: `--only <stage>` to run one stage, `--from <stage>` to
resume from one, `--force <stage>` to redo a stage whose fingerprint is
otherwise satisfied.

## Step 4 — report what came out

A finished run produces, under `work/<video-id>/`:

- `clips/` — 3 vertical (9:16, 20–60 s) and 2 horizontal (16:9, 60–120 s)
  MP4s with captions and cover images
- `posts.json` — per-platform copy, each tied to a verbatim transcript quote
- `publish/` — signed payloads
- `report.html` — which hook won, and what share of the difference each
  metric explains

Tell the user where the clips are and what the report concluded. If any
clip carries a note about a suppressed overlay or a relaxed threshold,
surface it — those are the pipeline being honest about a compromise, and
they are exactly what a human should look at.

## Publishing and credentials

**Publishing defaults to dry-run and writes the exact bytes it would
send.** A full run is safe with no credentials configured.

Going live needs no code change — credentials arrive through `.env` (copy
`.env.example`) and the mode switch in `config/config.yaml`. Without
credentials, metrics are labelled `SIMULATED`; with YouTube OAuth
configured they become `REAL`, including the only genuine 3-second
retention curve in the system. `docs/GERCEK-VERI.md` has that walkthrough.

**Never put credentials in `config/config.yaml` or in any committed
file.** If the user pastes a client secret into the conversation, write it
to `.env` and tell them it is gitignored.

## Retargeting to another brand

`config/brand.json` holds colours, fonts, logo, voice, banned phrases and
destination URL. Replacing it and the files in `assets/` retargets the
whole pipeline — no code change. Note that `fonts.display` (filename) and
`fonts.display_family` (the family name inside the font's `name` table)
must both be updated together; a mismatch makes libass substitute a font
silently, and a font without Turkish glyphs renders tofu boxes into a
clip you only notice after publishing.
