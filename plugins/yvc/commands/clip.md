---
description: Clip a long video into vertical and horizontal social posts
argument-hint: <youtube-url-or-file>
---

Use the `clip-video` skill to turn this video into social clips:

**$ARGUMENTS**

Follow the skill's steps in order. Specifically:

1. Locate or install the checkout, and stop with the exact fix command if
   Python 3.12, ffmpeg-with-libass or the `claude` CLI is missing.
2. Run `yvc doctor` and resolve anything it names *before* starting a run.
3. Warn the user that a 60-minute source takes ~2–2.8 hours, that
   transcription dominates, and that buffered silence is not a hang.
4. Start the run in the background, then report where the clips landed and
   what `report.html` concluded.

If no URL was given above, ask for one before doing anything else.
