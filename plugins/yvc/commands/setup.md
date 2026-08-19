---
description: Install or repair the YVC pipeline on this machine
---

Set up the video clipping pipeline using the `clip-video` skill's
installation steps, without running a video afterwards.

1. Run `tools/install.ps1` (idempotent — safe on an existing checkout).
2. Run `yvc doctor` and show the user its full output.
3. Report clearly which of the three unautomatable prerequisites are
   missing — Python 3.12, ffmpeg built with libass, the authenticated
   `claude` CLI — with the exact install command for each.
4. Point the user at `.env.example` if they intend to publish for real or
   collect real YouTube metrics, and remind them `.env` is gitignored.

Finish by telling them the one command that runs a video:
`.venv/Scripts/yvc.exe run "<url>"`
