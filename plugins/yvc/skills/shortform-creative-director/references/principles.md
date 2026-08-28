# Creative principles

Read this before diagnosing a clip. It's the "why" behind the workflow in
SKILL.md -- the platform research and creator analysis that should shape
how you read a clip, not a checklist to apply mechanically.

## The core tension

Short-form editing advice tends to collapse into one bad rule: "more edits
= better video." It doesn't. Two clips can both be excellent with
radically different edit density -- a MrBeast reveal cutting every few
seconds, and a Hormozi monologue holding a single frame for forty seconds
-- because in each case the *content itself* is doing a different amount
of the work. Your first job on any clip is figuring out which situation
you're in:

- **The visual needs stimulation.** The frame is static and neutral while
  the speech is doing all the interesting work -- naming concrete things,
  making claims, escalating stakes -- and nothing on screen reflects it.
- **The content is already stimulating.** High idea density, strong
  delivery, a frame that's already earning attention on its own. Adding
  B-roll here doesn't add value, it interrupts a viewer who's already
  locked in.

Misreading which situation you're in is the single most common way this
skill fails: either over-editing content that didn't need it, or leaving
a flat, static clip untouched because "the words were fine."

## Structure: hook, expectation, delivery

YouTube's own retention guidance (Understand your content performance,
Measure key moments for audience retention) frames the arc as:

```
HOOK -> EXPECTATION -> DELIVERY -> ESCALATION -> PAYOFF
```

not the simpler INTRO -> CONTENT -> END. The opening seconds set an
expectation; everything after either delivers on it or loses the viewer.
Two operational implications:

- A retention **dip** usually marks a point where the viewer's expectation
  stopped being met -- a slow patch, a tangent, restated information.
  These are your best candidates for a pattern interrupt or a pacing fix.
- A retention **spike** usually marks something worth rewatching or
  sharing, or something that needed more visual clarification the first
  time through. If you have prior retention data for a similar clip,
  treat spikes as "protect and maybe echo," not "cut."

TikTok's Creative Codes describe the same shape as HOOK -> BODY -> CLOSE,
adding that the hook itself can run on suspense, surprise, emotion,
curiosity, unexpected information, movement, or visual novelty -- and that
vertical format has real constraints (safe zone for on-screen text,
caption legibility, a visual hierarchy that reads in under a second).
These are platform mechanics, not aesthetic preferences -- treat them as
constraints on *how* you execute an intervention, not reasons to add ads-
style aesthetics to organic content.

## Hook mechanisms

Before touching the hook, name the mechanism already in play (from
research on what makes Shorts hooks work): curiosity gap, contradiction,
surprise/unexpected result, a specific number or stake, a direct question,
fear of missing out, an open loop, payoff-first, a challenge, a
transformation, a strong claim, social proof, a pattern interrupt, or
visual mystery.

If the mechanism is already strong, your job is reinforcing it visually,
not replacing it. If it's strong verbally but visually inert (a flat
talking-head delivering a great line), that's your highest-value
intervention slot. If it's genuinely weak, check whether a stronger later
moment exists in `clips.json`'s `text`/`evidence_quote` that could be
brought forward -- but never invent a claim the transcript doesn't
support to manufacture a better hook.

## Creator archetypes

There's no single "viral style." Identify which of these the clip's
delivery already resembles before deciding an editing budget:

- **High-stimulation (MrBeast-style).** Front-loaded stakes, visual
  novelty, rapid escalation, pattern interrupts, B-roll as visual proof,
  reaction shots, motion graphics, frequent stimulus changes. Analyses of
  this style note interrupts appearing roughly every few seconds in this
  specific format -- treat that as a description of one archetype, never
  as a universal cut-frequency rule to impose on a different kind of clip.
- **High-information talking head (Hormozi-style).** Immediate speech, high
  idea density, contradiction or open-loop hooks, semantic cuts, selective
  zoom/reframe, strong captions with selective word emphasis, and
  deliberately *restrained* visual intervention. Shot breakdowns of this
  style show it can carry 40-90+ seconds with barely any B-roll because
  the information density itself is the retention mechanism.
- **Educational/documentary.** Evidence-driven B-roll, diagrams,
  screenshots, timelines, charts, visual metaphors, slower but purposeful
  pacing -- interventions here earn their place by making an abstract
  claim concrete, not by adding energy.
- **Storytelling/emotional.** Close-ups, silence, reaction shots,
  controlled pacing, music dynamics used sparingly -- restraint protects
  the emotional beat; a well-timed absence of intervention is often the
  correct call here.
- **Comedy.** Timing and pauses matter more than density; reaction shots
  and unexpected cutaways work, but only in service of the punchline --
  never step on a joke's timing with an unrelated pattern interrupt.

## Pacing is not speed

Evaluate pacing by semantic density, sentence length, visual monotony,
emotional intensity, and information density -- not a fixed cuts-per-
second target. A high-energy statement may want frequent visual change. A
powerful emotional statement may want you to hold the shot and do
*nothing*. A complex explanation may need a diagram it currently lacks. A
punchline usually wants a clean, undistracted frame. Treat "hold the
shot" as an active pacing decision, not an absence of one.

## Anti-patterns to actively avoid

These aren't edge cases -- they're the default failure mode of an
editing pass optimizing for "looks more produced" instead of "serves this
specific clip":

- Generic stock B-roll that's visually impressive but semantically
  disconnected from what's being said.
- A cut-frequency rule applied uniformly regardless of archetype.
- SFX on every single cut.
- Caption styling (all-caps everywhere, rainbow color, emoji spam,
  karaoke word-by-word highlighting) applied as decoration rather than
  meaning-driven emphasis.
- Zooming, punching in, or reframing on a rhythm rather than a reason.
- A generated or metaphorical visual that implies something the
  transcript doesn't support.
- Covering a speaker's face or an important on-screen detail with text or
  a graphic.
- Revealing an intentionally withheld answer early because you found a
  way to illustrate it.
- Treating "I could add something here" as sufficient reason to add it.
