---
description: Clip a long video into vertical and horizontal social posts
argument-hint: <youtube-url-or-file>
---

Use the `clip-video` skill to turn this video into social clips:

**$ARGUMENTS**

If no URL was given above, ask for one before doing anything else.

Follow the skill's steps in order. Specifically:

1. Determine the platform — Windows or macOS — before running anything;
   the installer and the entry-point path both differ. Linux is not
   supported.
2. Locate or install the checkout per the skill's Step 1, and stop with
   the exact fix command if Python 3.12, ffmpeg-with-libass or the
   `claude` CLI is missing.
3. Run `yvc doctor` and resolve anything it names *before* starting a run.
   On macOS this includes the brand font: Segoe UI Black is not present
   there, and render would stop ninety minutes in.
4. Warn the user that a 60-minute source takes ~1h50m (measured), that
   transcription is 60–90 minutes of it and dominates everything else,
   and that buffered silence is not a hang. Do not kill a running
   pipeline to check on it — resume works, but a killed run wastes real
   time.
5. Start the run in the background and watch the stage artifacts under
   `work/<video-id>/` rather than stdout.
6. Report where the clips landed and what `report.html` concluded,
   including any note about a suppressed overlay or a relaxed threshold —
   those are the pipeline being honest about a compromise and are exactly
   what a human should look at.
