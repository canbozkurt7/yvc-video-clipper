# WeftCut execution mapping

Read this once Step 0 confirms a WeftCut MCP server is connected. It maps
`references/interventions.md`'s taxonomy to WeftCut's real tool names and
real units, and names the two things WeftCut genuinely cannot do so you
don't paper over them. Source: WeftCut's own `docs/mcp.md`
(github.com/UncleChair/WeftCut) -- if the connected server's advertised
catalog (`tools/list`, or the shim's `list-tools` subcommand) disagrees
with anything here, the live catalog wins; this file can drift as WeftCut
ships new versions.

## Units and coordinate spaces -- get this wrong and every timestamp is off

- **All `t_us` fields are microseconds**, not seconds and not
  milliseconds. Convert: `t_us = round(seconds * 1_000_000)`. A clip's
  intervention at 15.2s-16.5s is `t_start_us: 15200000, t_end_us:
  16500000`.
- Layer placement (`add_video_layer`, `add_motif`, keyframe `t_us` in
  `set_keyframe`) is **timeline-absolute** -- relative to the WeftCut
  project's own timeline, not the source clip. If you imported the clip at
  timeline position 0, clip-relative and timeline-absolute coincide; if
  it's placed elsewhere, add the layer's own start offset.
- Analysis tools (`analyze_clip`, `compare_frames`, `media://{id}/
  frame/{t_us}`) use **source-absolute** time -- relative to the imported
  media file itself, independent of where it sits on the timeline. Don't
  mix the two spaces: a shot boundary from `analyze_clip` is a source
  offset, not a timeline offset, until you account for where the layer
  starts.

## Intervention -> tool call

| Intervention type | WeftCut tool(s) | Notes |
|---|---|---|
| B-roll / image / screenshot | `import_media { path }` -> `add_video_layer { track_id, media_id, t_start_us, t_end_us, src_in_us, src_out_us }` (video) or the image equivalent | Requires a real file already on disk. See "What WeftCut cannot do" below -- it does not fetch or generate the asset for you. |
| Text overlay / statistic / quote / caption emphasis | `add_motif` (for a styled/animated text treatment -- check `list_motifs()` for a built-in lower-third/text Motif first) or a `Text` layer via `add_video_layer`-equivalent creation, then `update_layer_params` with `content`, `font_family`, `font_size_px`, `color`, `x`, `y`, `align`, `valign`, `box_w`/`box_h` | `update_layer_params` on Text has no scale fields on purpose -- resize by changing `box_w`/`box_h`/`font_size_px`, not `scale_x`/`scale_y`. Animate a text layer's size instead through `set_keyframe` on `scale_x`/`scale_y` if you need it to grow/shrink over time. |
| Full-transcript captions | `apply_subtitles { body, format?, t_start_us?, t_end_us? }` | Give it the SRT/VTT/ASS body directly (built from the clip's word timings); it builds the caption track itself. `t_start_us`/`t_end_us` are accepted for wire stability but ignored -- cue timing comes from the body, so get the body's own timings right. |
| Punch-in / punch-out / zoom / reframe | `set_keyframe { layer_id, param_key: "scale_x", t_us, value }` + the matching `scale_y` key (or `set_scale_linked` first if they should move together), each with `interp` chosen from the easing presets in `references/interventions.md`'s pacing guidance -- a hard punch-in wants `hold` or a short `ease_out`, not `linear` | Two keyframes minimum: the pre-punch scale and the punch-in scale a beat later. Read `get_param_track` first if the layer already has keyframes on that param -- `set_keyframe` inserts/updates in place, it doesn't wipe the track. |
| Pattern interrupt via a hard cut / jump cut | `split_layer { layer_id, at_t_us }` then treat the two halves independently (move one, add a layer between them, etc.) | `auto_split_by_shot { layer_id }` does this for every real shot boundary in one call if the interrupt should land on an existing cut rather than a new one you're choosing. |
| Freeze frame | Duplicate the frame as a still: `duplicate_layer` on a 1-frame trim, or an `ImageOverlay` from `media://{id}/frame/{t_us}` if you need it decoupled from the source clip | WeftCut has no single "freeze frame" tool; compose it from the primitives. |
| Color/mood effect (chromakey, brightness, contrast, saturation, sharpen, blur) | `add_effect { layer_id, kind }` then `update_effect { layer_id, effect_id, patch: { params: { mode: "Static", value } } }` | Order matters for the visible result: `move_effect` to reorder the chain if you add more than one. Keyframe an effect param the same way as any other: `set_keyframe` with `param_key: "effects[<effect_id>].params[<key>]"`, but only after a static value exists on that key (`UnknownKeyframeParam` otherwise). |
| Sound design (impact, whoosh, riser, silence) | Not in the MCP tool surface at all -- WeftCut's audio tools are gain/pan/fades on layers already present (`update_layer_params` for Audio: `gain_db`, `pan`), not an SFX library | Write these as EDL recommendations only; there's nothing to execute here regardless of connection. |
| Silence-trimming / tightening dead air | `detect_silences { layer_id }` -> `split_layer` + `delete_layer` per gap (or invoke the `/cut-silences` prompt, which does exactly this) | Only relevant if pacing diagnosis flagged real dead air, not as a default pass over every clip. |
| Transcription (if the upstream transcript is missing or you need clip-local word timings) | `transcribe_clip` (surfaced via the `/auto-caption` prompt) | YVC clips already carry a transcript from `transcript.json` -- prefer that; don't re-transcribe what's already been transcribed upstream with its own accuracy work. |

## Workflow order

1. `checkpoint { label }` before the pass, `lock_history { reason }` while
   editing, `unlock_history()` after.
2. `import_media { path }` once per new asset; reuse the returned
   `media_id` for every layer that asset needs.
3. For layer-graph ops that support it (`add_color_layer`,
   `add_video_layer`, `update_layer`, `update_layer_params`, `move_layer`,
   `split_layer`, `delete_layer`), consider `dry_run { operations }` first
   on a batch -- it validates against a clone and halts at the first
   error without committing anything. Motifs, captions, and effects aren't
   dry-runnable; call those for real and re-read `project://current`
   afterward to confirm the result rather than assuming success.
4. Commit the real calls. Watch for `LayerOverlap` and similar structured
   errors -- they carry an `options` array with concrete remedies
   (`create_new_track`, `trim_existing`, `split_at_t`); act on one rather
   than retrying the same call unchanged.
5. `checkpoint { label }` again after, so the whole pass is bracketed and
   restorable as a unit.

## What WeftCut cannot do -- name these, don't paper over them

**No export/render tool.** WeftCut's docs say this outright: *"there are
intentionally no `render_export` / `cancel_render` MCP tools. Agents that
need a render either ask the user, or read `project://compiled` to inspect
what the audio export would produce."* This is a deliberate design choice
by WeftCut, not a missing integration on this skill's part. Building the
timeline is the end of what this skill can do; getting a final MP4 out of
WeftCut is a human clicking Export. Say so in the report every time (Step
5) -- don't let "the timeline is built" read as "the clip is done."

**No asset sourcing.** `import_media` needs a `path` to a file that
already exists. WeftCut has no stock-footage search, no image generation,
no B-roll library. If a `broll`/`image`/`generative` intervention has
nowhere to pull a real file from in this session, write it to the EDL as
a recommendation (with a concrete `query` describing what's needed) and
do not execute it -- and say in the report that it's a recommendation
pending an asset, not a completed edit. Interventions that are pure
WeftCut primitives -- text, motifs, keyframed zoom/reframe, cuts on
existing footage, effects, captions -- have no such gap and should be
built for real whenever WeftCut is connected.
