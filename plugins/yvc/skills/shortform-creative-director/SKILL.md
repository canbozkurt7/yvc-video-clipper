---
name: shortform-creative-director
description: Turn an already-produced short-form clip (from the YVC clip pipeline, or any 30s-2min vertical/horizontal clip with a transcript and a hook) into a precise, timestamped edit-decision list -- B-roll, text, zooms, pattern interrupts, sound design -- ready for a downstream video-editing tool to execute. Use this whenever a clip already exists and the task is to decide HOW it should be edited/restyled, not to find or cut the clip in the first place. Trigger on "creative director", "edit decisions", "EDL", "B-roll opportunities", "make this clip more engaging", "restyle this clip", "Vidmoat", "WeftCut", or when a YVC run has clips.json/render.json and the next question is what to do with the finished clip.mp4. Do NOT use this to select segments from a long video or generate the clip itself -- that is a separate, upstream stage.
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
and pattern-interrupt decision rules, and the scoring rubric.

## Where this fits

```
long-form video -> clip generator -> 30s-2min clip + transcript + hook
                                              |
                                              v
                              YOU (this skill): decide edits
                                              |
                                              v
                          video editing MCP (Vidmoat / WeftCut / etc.)
                                              |
                                              v
                                    final restyled clip
```

Everything upstream of the clip already happened. Do not re-score the hook,
re-cut the segment boundaries, or second-guess *which* moment was chosen --
that decision was made by a separate stage with its own defensible rubric.
Your job starts once the clip is a fact.

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
is already there. Extract frames with ffmpeg and read them as images:

```powershell
ffmpeg -i <clip.mp4> -vf fps=1 -q:v 3 <tmp_dir>\frame_%04d.jpg
```

One frame per second is enough to diagnose "is this static talking head or
is something already changing on screen" for most clips; sample more
densely (every 0.5s) around a moment you're specifically weighing an
intervention for. Read the frames as images before deciding anything --
skipping this step and reasoning from the transcript alone is exactly the
failure mode this skill exists to avoid.

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

Write **semantic** decisions ("insert B-roll of a Tesla Model 3 driving"),
not tool calls. The one exception: if a video-editing MCP (Vidmoat,
WeftCut, or similar) is actually connected in this session -- check with
ToolSearch for tool names containing those or "video edit" before you
start -- prefer phrasing `purpose`/`query` fields in terms that tool's own
operations can consume directly, and say in the report which tool you
targeted. Never fabricate a tool call to a server that isn't connected.

## What "done" looks like

A good run for a clip that's already well-shot might legitimately produce
zero or one intervention. A good run for a stakes-heavy reveal clip might
produce five or six. Neither count is inherently right -- what's wrong is
producing the same number of edits regardless of what the clip actually
needs, or padding the JSON with low-priority interventions to look
thorough. If you rejected candidates, that rejection is part of the value
of the run; consider noting a one-line summary of what you considered and
passed on, so a person skimming the report sees restraint was a choice.
