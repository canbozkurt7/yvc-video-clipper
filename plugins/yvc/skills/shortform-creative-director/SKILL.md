---
name: shortform-creative-director
description: Turn an already-produced short-form clip (from the YVC clip pipeline, or any 30s-2min vertical/horizontal clip with a transcript and a hook) into a precise, timestamped edit-decision list -- B-roll, text, zooms, pattern interrupts, sound design -- and, when a WeftCut MCP server is connected, actually build it on the WeftCut timeline (import media, place layers, keyframe, caption) rather than just describing it. Use this whenever a clip already exists and the task is to decide HOW it should be edited/restyled, not to find or cut the clip in the first place. Trigger on "creative director", "edit decisions", "EDL", "B-roll opportunities", "make this clip more engaging", "restyle this clip", "Vidmoat", "WeftCut", or when a YVC run has clips.json/render.json and the next question is what to do with the finished clip.mp4. Do NOT use this to select segments from a long video or generate the clip itself -- that is a separate, upstream stage.
---

# Short-form video creative director

You are being asked to do one specific job inside a larger pipeline: a clip
already exists. Your job is to decide what should happen to it before it
ships -- not to make it, not to publish it, not to ask the clip's owner to
approve each cut. Decide, timestamp, and hand off.

**The default answer for any given moment is "leave it alone."** Every
intervention you propose has to earn its place. A talking head delivering a
dense, well-paced statement needs zero B-roll; adding it would dilute the
idea, not sell it. The bar is not "could I add something here" -- it is
"does this specific viewer moment get measurably better if I do."

Read `references/principles.md` before your first pass on a clip in a new
session -- it carries the platform research (YouTube/TikTok guidance, hook
mechanisms, creator archetypes) that should shape your read of the clip.
Read `references/interventions.md` when you're past diagnosis and choosing
which specific intervention to use -- it has the full taxonomy, the B-roll
and pattern-interrupt decision rules, and the scoring rubric. Read
`references/weftcut.md` as soon as you detect a WeftCut MCP server is
connected (see Step 0) -- it maps every intervention type to WeftCut's
actual tool calls, in its actual units, and names the two things WeftCut
cannot do for you.

## Where this fits

```
long-form video -> clip generator -> 30s-2min clip + transcript + hook
                                              |
                                              v
                              YOU (this skill): decide edits
                                              |
                                              v
                       WeftCut MCP: build the timeline for real
                       (import, place, keyframe, caption -- NOT export)
                                              |
                                              v
                         human clicks Export in the WeftCut UI
                                              |
                                              v
                                    final restyled clip
```

Everything upstream of the clip already happened. Do not re-score the hook,
re-cut the segment boundaries, or second-guess *which* moment was chosen --
that decision was made by a separate stage with its own defensible rubric.
Your job starts once the clip is a fact.

**The last arrow above is not automatable, and that's WeftCut's own design,
not a gap in this skill.** Its MCP surface deliberately ships no
`render_export` tool -- see `references/weftcut.md` &sect; the export gap
for exactly what that means and how to hand off cleanly instead of
pretending it isn't there.

## Step 0 -- is WeftCut actually connected?

Before anything else, check with ToolSearch for tool names like
`import_media`, `add_video_layer`, `add_motif`, `apply_subtitles`, or a
server literally named `weftcut`. This changes almost everything below:

- **Connected** -- you're not just advising, you're editing. Read
  `references/weftcut.md` now. Use WeftCut's own resources
  (`analyze_clip`, `describe_clip`, `media://{id}/frame/{t_us}`) for Step 1
  instead of manual ffmpeg frames -- they're already there, machine-
  readable, and `describe_clip` gets you scene descriptions ffmpeg frames
  alone don't. Step 4 becomes "build it," not "describe it."
- **Not connected** -- fall back to ffmpeg frame extraction below and
  produce the semantic EDL files only. Don't fabricate WeftCut tool calls
  to a server that isn't there.

## Step 1 -- gather the inputs

If you're working inside a YVC pipeline run, the inputs already exist on
disk. Given a `video_id` and `clip_id`:

- `work/<video_id>/clips.json` -- find the clip by `clip_id`. It carries
  `start`/`end` (source-video seconds), `hook_type`, `hook_line`,
  `evidence_quote`, `source_segment`, `text` (the clip's own transcript
  text), `selected_reason`. This is the semantic ground truth -- never
  invent a claim that isn't in `text`.
- `work/<video_id>/render.json` -- find the same `clip_id`'s result for the
  actual rendered file (`path`), `duration_s`, `aspect`, and any
  `crop_stats` (whether reframing already tracks the speaker).
- `work/<video_id>/transcript.json` -- word-level timings
  (`segments[].words[]`, each `{"w", "start", "end", "p"}`) in
  source-video time. Slice to `[clip.start, clip.end]` and subtract
  `clip.start` from every timestamp so your EDL is clip-relative (0 =
  first frame of the clip, matching what a video editing tool expects).

Called with a bare video file, transcript and hook outside YVC, use those
directly -- the contract is the same three things (video, transcript,
hook), just not read from these specific file paths.

**You must actually look at the video, not just the transcript.** The
transcript tells you what was said; it says nothing about what's on
screen, whether the frame is already doing visual work, or whether a cut
is already there. How you look depends on Step 0:

- **WeftCut connected:** `import_media { path: clip.mp4 }` first (you need
  the `media_id` for everything downstream anyway), then use
  `analyze_clip` for deterministic shot boundaries + per-shot brightness/
  motion/sharpness, `describe_clip` for scene descriptions where a video-
  understanding backend is configured, and `media://{id}/frame/{t_us}` to
  pull specific frames on demand (`t_us` = seconds x 1,000,000,
  source-absolute). This is strictly better than manual sampling -- it's
  already structured and it's free of the guesswork in picking which
  seconds to sample.
- **Not connected:** extract frames with ffmpeg and read them as images:

  ```powershell
  ffmpeg -i <clip.mp4> -vf fps=1 -q:v 3 <tmp_dir>\frame_%04d.jpg
  ```

  One frame per second is enough to diagnose "is this static talking head
  or is something already changing on screen" for most clips; sample more
  densely (every 0.5s) around a moment you're specifically weighing an
  intervention for.

Either way, read the frames as images before deciding anything -- skipping
this step and reasoning from the transcript alone is exactly the failure
mode this skill exists to avoid.

## Step 2 -- diagnose before deciding

Before proposing a single intervention, answer these for yourself (they
don't need to appear in the output, but skipping them produces generic
edits):

- What is the hook mechanism already in play (`hook_type` +
  `hook_line`/`evidence_quote`)? Is it already strong on its own, strong
  but visually under-supported, or genuinely weak? Don't rewrite a hook
  that's already working.
- Which creator archetype fits this clip -- high-stimulation, high-density
  talking head, educational/documentary, storytelling, comedy? (See
  `references/principles.md` &sect; archetypes.) This determines your
  editing budget: a Hormozi-style dense monologue earns far fewer
  interventions than a MrBeast-style stakes reveal, and treating them the
  same is the most common way this skill goes wrong.
- Segment the clip into narrative beats by what each stretch is *doing*
  (HOOK, SETUP, CLAIM, PROOF, CONTRADICTION, REVEAL, PAYOFF, ...), not by
  fixed time slices. A beat can be two seconds or twenty.

## Step 3 -- decide interventions, beat by beat

For each beat, ask: does the current visual already deliver, or is there a
concrete gap (an unillustrated object, person, number, or claim; a flat
stretch where the narrative escalates but the frame doesn't; a payoff with
no visual landing)? Pull from the taxonomy in `references/interventions.md`
only when the gap is real. Score every candidate you seriously consider
(retention/comprehension/emotional/novelty/narrative-fit/distraction-risk,
1-10 each per `references/interventions.md`) and keep only the ones whose
priority clears the bar -- reject the rest explicitly rather than silently
dropping them, so the reasoning is auditable.

Protect open loops: if the clip is withholding an answer on purpose, do
not visually reveal it early just because you can illustrate it.

## Step 4 -- write the output

Every run produces two files, both keyed by `clip_id`, both written even
when the decision is "no interventions" (that's a valid, useful result --
it says the clip was reviewed, not skipped):

- `work/<video_id>/creative_direction/<clip_id>.md` -- human-readable
  report. One block per intervention (and, if none, one line saying why
  the clip needs none):

  ```
  TIMESTAMP
  00:17.4-00:19.2

  CURRENT
  Speaker discusses Tesla sales; frame is a static talking head.

  DECISION
  Insert Tesla Model 3 B-roll.

  WHY
  Concrete, visually distinct object named by name; a static frame is
  doing no work while the viewer pictures something the video could
  just show them.

  TYPE: B-ROLL   PRIORITY: HIGH
  EXPECTED EFFECT: comprehension, novelty
  ```

- `work/<video_id>/creative_direction/<clip_id>.json` -- machine-readable
  EDL. Exact schema in `references/interventions.md` &sect; output schema;
  the shape is `{clip_id, creative_strategy, interventions[]}` with every
  timestamp clip-relative in seconds.

Write **semantic** decisions first regardless of Step 0 ("insert B-roll of
a Tesla Model 3 driving") -- the EDL is the audit trail of what you decided
and why, independent of whether you also went and built it.

**If WeftCut is connected, also build it.** Don't stop at the EDL. Per
`references/weftcut.md`:

1. `checkpoint { label: "before creative-direction pass" }` so the whole
   batch is one clean undo step.
2. `lock_history { reason: "creative director editing pass" }` while you
   work, `unlock_history()` when done -- keeps a concurrent user/agent from
   reverting mid-batch.
3. Translate each *kept* intervention (never the rejected ones) into the
   matching tool call(s) from `references/weftcut.md`'s mapping table.
   `dry_run` the batch first where the op types support it (layer adds/
   updates/moves/splits/deletes do; motifs, captions and effects don't --
   call those directly and verify by re-reading `project://current` after).
4. Never fabricate a media asset. WeftCut places files that already exist
   on disk (`import_media { path }`); it does not source B-roll from a
   query string. An intervention whose `type` is `broll` and for which no
   asset file is available is written to the EDL as a **recommendation**
   with its `query`, not executed -- say so explicitly in the report
   rather than silently skipping it or, worse, importing the wrong file to
   avoid leaving a gap.

## Step 5 -- hand off for export, explicitly

Once the timeline changes are committed (or the semantic-only EDL is
written, if WeftCut isn't connected), the run is NOT finished by producing
a final video file -- WeftCut's MCP surface has no export tool by design
(`references/weftcut.md` &sect; the export gap), so a human has to open the
project and click Export. End every run, in the report, with an explicit
line naming this as the one required human step -- e.g. "Timeline changes
committed to WeftCut (checkpoint `<id>`). Open the project and export when
ready; no further agent action will produce the final file." Do not imply
the clip is done and ready to publish when what actually happened is that
the timeline was edited.

## What "done" looks like

A good run for a clip that's already well-shot might legitimately produce
zero or one intervention. A good run for a stakes-heavy reveal clip might
produce five or six. Neither count is inherently right -- what's wrong is
producing the same number of edits regardless of what the clip actually
needs, or padding the JSON with low-priority interventions to look
thorough. If you rejected candidates, that rejection is part of the value
of the run; consider noting a one-line summary of what you considered and
passed on, so a person skimming the report sees restraint was a choice.
