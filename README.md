# YVC — No-Touch Video Clipping Pipeline

Turns one long-form Turkish video into publishable social clips from a
single command. Download, transcription, semantic segmentation, hook
scoring, speaker-tracked vertical reframing, burned-in captions,
per-platform copy, scheduling, publishing, measurement and a feedback
loop — with no manual step in between.

```bash
yvc run "https://www.youtube.com/watch?v=r39OrneyMDs"
```

Everything after that command is automatic. Publishing defaults to
dry-run, so the pipeline is safe to execute end to end before any social
credentials exist.

| Doc | What it covers |
|---|---|
| [docs/MIMARI.md](docs/MIMARI.md) | Architecture diagram and per-stage tooling |
| [docs/STRATEJI-NOTU.md](docs/STRATEJI-NOTU.md) | Strategy note: criteria, costs, limits |
| [docs/GERCEK-VERI.md](docs/GERCEK-VERI.md) | Collecting **real** YouTube metrics |
| [docs/DURUM.md](docs/DURUM.md) | Current state and handoff notes |
| [docs/IKINCI-MAKINE.md](docs/IKINCI-MAKINE.md) | Setting up on a second machine, and what git deliberately does not carry |
| [NOTICE.md](NOTICE.md) | Third-party assets, licences, and what is deliberately not committed |

---

## Quick start

On a fresh Windows machine, three commands:

```powershell
git clone https://github.com/canbozkurt7/yvc-video-clipper.git
```

```powershell
powershell -ExecutionPolicy Bypass -File yvc-video-clipper/tools/install.ps1
```

```powershell
claude
```

The installer handles everything it can: Python 3.12, ffmpeg, the
`claude` CLI, yt-dlp, Deno, the virtualenv, the package, then `doctor`.
The third command exists only to **sign in to `claude`** — a browser
round-trip that cannot be scripted, and the one manual step in the whole
setup.

Then run a video:

```powershell
cd yvc-video-clipper
```

```powershell
.venv\Scripts\yvc.exe run "https://www.youtube.com/watch?v=<id>"
```

Nothing after that needs a keystroke. No config file needs editing —
`cpu_threads` resolves to the machine's physical core count on its own.
Publishing defaults to dry-run, so this is safe to run end to end before
any social credentials exist.

Output lands in `work/<video_id>/`: the clips under `clips/`, the
per-platform copy in `posts.json`, the report in `report/`, and the
publish payloads in `publish/`.

---

## Requirements

| Tool | Notes |
|---|---|
| Python 3.12 | 3.13 is not supported by the pinned CTranslate2 build |
| ffmpeg / ffprobe | Must be built with `libass`, `fontconfig`, `freetype`. Verified by `yvc doctor` |
| yt-dlp | Installed by `tools/bootstrap.py` into `tools/bin/`, or supply a path in `config/config.yaml` |
| Deno | Installed by the same script. Required by yt-dlp to solve YouTube's n-signature challenge — without it only 360p formats resolve, with no error. See Troubleshooting |
| `claude` CLI | Authenticated. Used as the LLM engine; no API key needed |
| A Turkish-capable font | Resolved from the system font directory, not shipped. Automatic on Windows; see [NOTICE.md](NOTICE.md) elsewhere |

CPU-only by design. There is no GPU dependency anywhere in the pipeline.

Developed and verified on Windows 11. The pipeline is not
platform-independent yet: process-tree termination (`taskkill`), the npm
shim invocation of the `claude` CLI, and the run lock (`msvcrt`) are
Windows-specific. Everything else — ffmpeg, faster-whisper, the scoring
and selection logic — is portable.

---

## Install as a Claude Code plugin

The fastest path: hand Claude Code the repo and let the skill do the setup.

```
/plugin marketplace add canbozkurt7/yvc-video-clipper
/plugin install yvc@datassist
/yvc:setup
```

Then clip a video by asking in plain language, or:

```
/yvc:clip https://www.youtube.com/watch?v=<id>
```

The skill locates or installs a working checkout, runs `doctor`, sets
expectations about the ~1h50m runtime, and reports what came out. It
deliberately installs the checkout **outside** the plugin cache: a run
writes ~1 GB into `work/`, and a plugin update would wipe it.

> This repository is private. `/plugin marketplace add` uses your existing
> git credentials, so it works for you and anyone you invite as a
> collaborator. Make the repo public if you want it to work for anyone
> with the link.

### Or install directly

```powershell
git clone https://github.com/canbozkurt7/yvc-video-clipper.git
./yvc-video-clipper/tools/install.ps1
```

`install.ps1` is idempotent — re-running it pulls, re-verifies and repairs.
It creates the virtualenv, installs the package, fetches yt-dlp and Deno,
and runs `doctor`. If pip cannot reach PyPI (TLS interception — see below)
it falls back to the curl-based wheelhouse automatically.

It also installs the three prerequisites that cannot ship in a git repo —
**Python 3.12**, **ffmpeg**, and the **`claude` CLI** — via winget and
npm. Pass `-NoInstall` to have them reported rather than installed, for
machines where those are managed by something else.

The only step left to a human is **signing in to `claude`**: run it once
and authenticate in the browser. Nothing else in setup is manual.

---

## Install manually

```bash
git clone <this-repo> && cd yvc
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .
./.venv/Scripts/python.exe tools/bootstrap.py
./.venv/Scripts/yvc.exe doctor
```

`bootstrap.py` fetches yt-dlp and Deno into `tools/bin/`. They are not
committed: together they are ~110 MB of per-platform binaries, and yt-dlp
goes stale within weeks as YouTube changes its player. Re-run it with
`--force` when downloads start failing.

`doctor` is the gate. It probes ffmpeg's filters, the `claude` CLI's
invocation form, font glyph coverage for `ç ğ ı İ ö ş ü`, and free disk,
and prints exactly what is missing. If it passes, a full run will not
fail on setup.

Then:

```bash
./.venv/Scripts/yvc.exe run "https://www.youtube.com/watch?v=r39OrneyMDs"
```

Publishing defaults to dry-run, so this is safe to execute end to end
with no credentials configured. Nothing in the run requires a keystroke.

Measured on a 4-core 15 W laptop, 60-minute source:

| Stage | Time |
|---|---|
| acquire (729 MiB) | 14 min |
| **transcribe** | **52 min** (`small`, int8, RTF 1.16) |
| segment + score (LLM) | 24 min |
| render (5 clips) | 4 min |
| copywrite | 14 min |
| **total** | **~1 h 50 min** |

Transcription is half the run and is memory-bandwidth bound, not
compute bound: `small` → `medium` is 2.2× the parameters but 10× the
time. That is why `small` is forced rather than chosen here. See
[docs/IKINCI-MAKINE.md](docs/IKINCI-MAKINE.md) for the per-tier
measurements and the two routes to `large-v3`.

### If pip cannot reach PyPI

On networks running TLS inspection (this project was built behind a
Fortinet FortiGate), pip, uv and anything else using OpenSSL will fail
with:

```
SSLCertVerificationError: ... root certificate which is not trusted
```

`--trusted-host`, `--use-feature=truststore` and `uv --system-certs` all
fail too, because the index response never arrives. curl, which uses
Schannel, is unaffected. Use the bundled bootstrapper, which downloads
wheels with curl and then installs offline:

```bash
./.venv/Scripts/python.exe tools/wheelhouse.py faster-whisper opencv-python-headless numpy pydantic PyYAML rich tenacity psutil typer jinja2 structlog filelock regex fonttools pytest
```

pip still performs all version solving; the tool only supplies the files.

---

## Configuration

| File | Contains |
|---|---|
| `config/config.yaml` | Pipeline behaviour: model tier, thresholds, render settings, retention |
| `config/brand.json` | Everything brand-editable: colours, fonts, logo, voice, banned phrases, destination URL |
| `.env` | Secrets only. Never commit — copy `.env.example` and fill in what you have |

Precedence: CLI flag > environment variable > `config.yaml` > defaults.

Retargeting the pipeline at a different brand means replacing
`config/brand.json` and the files in `assets/` — no code changes.

---

## How it works

```
acquire → transcribe → turkish → speakers → segment → score → select
        → render → copywrite → schedule → publish → collect → report → feedback → gc
```

Each stage reads named artifacts and writes named artifacts atomically.
A stage is skipped when its fingerprint — its own version, the config
subtree it reads, and the fingerprints of its dependencies — is unchanged
and its outputs exist. So `yvc run <url>` twice does no redundant work,
and editing a copywriting weight re-runs copywriting onward without
touching the hour-long transcription.

### Hook scoring

The brief this was built against rejects "the model chose it" as an
explanation, so scoring is split. **45 points are deterministic**,
computed from the waveform and the text, identical on every run.
**55 points are LLM-judged**, and every one of those requires a written
justification quoting the transcript.

| Criterion | Weight | Type |
|---|---|---|
| Vocal energy dynamics | 8 | deterministic |
| Pitch variance | 6 | deterministic |
| Speech rate and rate change | 5 | deterministic |
| Numeric / concrete-fact density | 7 | deterministic |
| Question density | 6 | deterministic |
| Turn-taking liveliness | 5 | deterministic |
| Opening self-containedness | 8 | deterministic (penalty) |
| Strength of the first 3 seconds | 14 | LLM |
| Curiosity gap and payoff | 12 | LLM |
| Emotional charge / contrarian claim | 10 | LLM |
| Standalone comprehensibility | 11 | LLM |
| Audience fit | 8 | LLM |

The LLM never sees the deterministic scores — otherwise it anchors on
them and the two signal families stop being independent. The two
self-containedness criteria are deliberate counterweights: without them
the rubric selects dramatic fragments that open with "ve bu yüzden…" and
mean nothing to someone arriving cold.

`scores.json` records, per segment: every raw measurement, every score,
the weight applied, the LLM's reasoning, an `evidence_span` pointing at
real timestamps, and the prompt/response hashes.

### Why the LLM never emits a timestamp

A hallucinated timestamp lands mid-word. Instead, sentence boundaries are
derived deterministically (terminal punctuation, pauses over 0.65 s,
speaker changes — with Turkish ordinals and abbreviations excluded), each
is given an integer id, and the model returns **ids only**. Every
boundary is a real word start by construction.

---

## Turkish correctness

Turkish is not incidental here; diacritic accuracy is a graded output.

* `str.upper()` / `str.lower()` are **banned**. They are wrong for
  Turkish: `"istanbul".upper()` gives `ISTANBUL`, not `İSTANBUL`. Worse,
  `"İ".lower()` returns `i` plus U+0307 COMBINING DOT ABOVE — a
  two-codepoint string that breaks equality and lexicon lookups and
  renders as a floating dot in libass. All casing goes through
  `yvc.turkish.casing`.
* Everything is NFC-normalised at ingest.
* Diacritic density is checked against the 60–90 per 1000 characters that
  running Turkish text exhibits. Below 35 the stage fails loudly, because
  systematic ASCII-folding is an upstream problem that cannot be repaired
  token by token.
* Ambiguous diacritic restorations (`kar`/`kâr`, `acı`/`açı`) are left
  alone unless one candidate is far more frequent. Not guessing beats
  guessing.

UTF-8 is forced at five boundaries — stdout, child process environments,
file I/O, JSON, and the Windows console code page. `tests/encoding/`
runs the whole round-trip with the console forced to cp1252, including a
test asserting the original crash still reproduces without the bootstrap.

---

## Rendering

Vertical clips track the active speaker; a centre crop is explicitly not
acceptable. Faces are detected with OpenCV YuNet (a 232 KB ONNX model,
vendored so the pipeline never downloads at runtime), sampled at 6 fps and
**only inside selected clip ranges** — scanning the full hour would cost
15+ minutes to produce data that is 95% discarded.

The trajectory is smoothed in a specific order: deadzone → EMA → rate
limit → shot-cut snap → clamp → simplify. Each step earns its place; the
shot-cut snap is the opposite of smoothing, because interpolating across
a hard cut produces a slide that reads as a bug. When no face is detected
the window holds position rather than recentring.

`sendcmd` was rejected — it only produces step changes, so smooth motion
would need a command per frame. The crop `x` is instead an expression
evaluated per frame, built as a flat sum of clipped ramps:

```
x(t) = x0 + Σ (xi − xi−1) · clip((t − ti−1)/(ti − ti−1), 0, 1)
```

Two details that are load-bearing rather than stylistic:

* **`format=yuv420p` must be the last filter.** Both `ass` and `overlay`
  negotiate their own pixel format and will promote the stream to
  yuv444p, which every social platform rejects.
* **ffmpeg runs with its working directory set to the clip folder**, so
  `sub.ass` and `fonts` are bare relative names. No drive letter or
  backslash reaches the filter parser, which sidesteps the `C\:/path`
  escaping problem entirely instead of trying to escape through three
  levels of unquoting.

Captions are ASS, one Dialogue event per word with the active word
recoloured — exactly reproducible, no libass timing quirks, debuggable by
reading the file. ASS colours are `&HBBGGRR`, reversed from CSS hex; that
conversion lives in one tested function because getting it wrong produces
a plausible wrong colour rather than an error.

---

## Publishing

No social credentials are required to run the pipeline. `build_calls()`
is pure and shared; `DryRunAdapter` serialises those calls to disk and
`LiveAdapter` executes them. The dry-run artifacts are therefore the
exact bytes that would be sent, not mocks, and going live is a matter of
adding credentials to `.env` — no code changes. A platform with missing
credentials degrades to dry-run and is reported, rather than failing the
run.

## Measurement

`metrics/collectors/` reads real platform analytics where an integration
exists, and the simulator fills only the fields that came back empty —
per field, not per row. YouTube is the one platform exposing an audience
retention curve, which makes it the calibration anchor for 3-second hook
retention rather than merely one more destination.

Clips published outside the pipeline still yield real data: map their
platform ids in `work/<id>/remote_ids.json` and `collect` will join them.
Platforms without a collector state *why* in the registry, so a
measurement gap appears in the report as a named absence instead of a
blank cell. See [docs/GERCEK-VERI.md](docs/GERCEK-VERI.md).

---

## The feedback loop

Realised performance becomes a bounded per-hook-type multiplier that
scales the next video's rubric score: `S(c) = M(hook) · Σ wⱼ·sⱼ(c)`,
with `M` clipped to `[0.80, 1.25]`. A genuinely good clip carrying a
"losing" hook type still outranks a mediocre one — the learned signal
tilts the ranking, it never dictates it.

**Only measured outcomes teach.** The gate is the share of the HQS
composite backed by real data, not the row's provenance label. That
distinction is load-bearing: YouTube returns no impression count, so a
genuinely measured YouTube row is always `MIXED`, and gating on the label
would silently discard every real observation the pipeline can collect.
`hook_retention_3s` alone carries 0.45 of HQS against a 0.60 threshold,
which is the arithmetic way of saying nothing is learned without a real
retention curve. Learning from the simulator is refused outright — its
retention model is conditioned on hook type, so it would only teach back
its own assumptions while looking exactly like a working loop.

Until real metrics exist every multiplier is exactly `1.0` and scoring is
byte-identical to the rubric alone.

Three guards keep one hook type from winning forever: the bounds above,
Thompson sampling (rarely-used types keep a wide posterior and
periodically draw high), and a 20% exploration quota reserving slots for
types outside the current top two, tagged `selected_reason:
"exploration_quota"` so the report can separate exploration from
exploitation.

---

## Troubleshooting

**`UnicodeEncodeError` on Turkish text** — something bypassed
`yvc.bootstrap` or used bare `open()`. Both are covered by tests.

**yt-dlp only offers 360p** — YouTube's n-signature challenge needs a JS
runtime. Install Deno and put it on PATH; without it high-resolution
formats silently disappear from the format list.

**`HTTP 403` on a specific format** — try another player client via
`--extractor-args "youtube:player_client=web_embedded"`. Clients differ
in which formats they can actually fetch, and the working one changes
over time.

**Whisper fails to load / the run downgrades the model** — `large-v3`
needs roughly 3.6 GB resident. The preflight steps down the ladder and
records the reason in `quality_report.json` so a degraded run is never
mistaken for a clean one. Close other applications and re-run to get the
model you asked for.

**pip cannot reach PyPI** — see Install, above.

---

## Licence

MIT — see [LICENSE](LICENSE). Third-party assets and the reasoning
behind what is and is not committed are in [NOTICE.md](NOTICE.md).
