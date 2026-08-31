---
description: Install or repair the YVC pipeline on this machine
argument-hint: "[--no-install]"
---

Set up the video clipping pipeline using the `clip-video` skill's Step 1,
without running a video afterwards.

Extra arguments, if any: **$ARGUMENTS**

1. Work out the platform first — the rest differs by it:
   - **Windows** → `tools\install.ps1`, entry point `.venv\Scripts\yvc.exe`
   - **macOS** → `tools/install.sh`, entry point `.venv/bin/yvc`
   - **Linux** → not supported; say so rather than improvising.
2. Follow the skill's Step 1 to locate the checkout and pick where to
   install *from*. Prefer the marketplace clone with `-Source` /
   `--source` when it exists — it is already on disk, so this avoids a
   second full clone. Both installers are idempotent, so running one on
   an existing checkout is the safe move rather than a risky one.
3. Run `yvc doctor` and show the user its full output.
4. Report clearly which of the three unautomatable prerequisites are
   missing — Python 3.12, ffmpeg built with libass, the authenticated
   `claude` CLI — with the exact install command for each. Signing in to
   `claude` is a browser round-trip and cannot be scripted; if doctor
   reports the CLI as failing, that is the one thing to hand back to the
   user. There is no API key to look for.
5. **On macOS, check the brand font before declaring success.**
   `config/brand.json` names Segoe UI Black, which does not exist on a
   Mac, and the render stage will stop with `FontNotFound`. Tell the user
   to drop a Turkish-capable `.ttf` into `assets/fonts/` and update both
   `fonts.display` and `fonts.display_family` to match it.
6. Point the user at `.env.example` if they intend to publish for real or
   collect real YouTube metrics, and remind them `.env` is gitignored.
   Never write a credential into `config/config.yaml` or any other
   committed file.

Finish by telling them the one command that runs a video, in the form for
their platform:

- Windows: `& "$YVC\.venv\Scripts\yvc.exe" run "<url>"`
- macOS: `"$YVC/.venv/bin/yvc" run "<url>"`
