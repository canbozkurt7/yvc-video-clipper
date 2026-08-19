# Third-party assets and licences

The MIT licence in `LICENSE` covers the code in this repository. It does
not cover the things below, which is why several of them are fetched at
setup time rather than committed.

## Not redistributed — resolved at runtime

**Segoe UI / Segoe UI Black** (`config/brand.json` → `fonts.display`)
Microsoft licenses these for use *on* Windows, not for redistribution.
`.gitignore` excludes `assets/fonts/*.ttf`, and `src/yvc/render/fonts.py`
resolves the file from the system font directory instead. On Windows this
is automatic. On macOS or Linux the render stage fails with the full
search path and an instruction rather than rendering tofu boxes into a
published clip.

To retarget to a freely licensed font, drop the TTF in `assets/fonts/`
and set both `fonts.display` (the filename) and `fonts.display_family`
(the family name inside the font's `name` table — a mismatch makes libass
substitute silently). Fonts with verified Turkish coverage: Inter,
Montserrat, Source Sans 3, Noto Sans — all SIL OFL, all redistributable.

## Fetched by `python tools/bootstrap.py`

| Binary | Licence | Why not committed |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | ~17 MB, per-platform, and goes stale within weeks as YouTube changes its player |
| [Deno](https://github.com/denoland/deno) | MIT | ~40 MB per platform; required for YouTube's n-signature challenge |

## Expected on PATH, not vendored

**ffmpeg / ffprobe** — must be built with `libass`, `fontconfig`,
`freetype`. `yvc doctor` verifies this. Licence depends on the build
(LGPL or GPL); a GPL build is fine for use, and this repo does not link
against or redistribute it.

**`claude` CLI** — the LLM engine. Requires an authenticated Claude
subscription. No API key is used anywhere in the code.

## Committed, and belonging to their owners

| File | Note |
|---|---|
| `assets/models/face_detection_yunet_2023mar.onnx` | YuNet face detector, 227 KB, Apache 2.0 (OpenCV Zoo) |
| `assets/logo-*.png`, `config/brand.json` | Datassist brand assets. Replace both to retarget the pipeline at another brand — no code change is needed |

## Content

The pipeline downloads and re-cuts third-party video. Whether you may
publish those clips is a question about *that* video's rights, not about
this software. The default source is a video the operator has rights to.
