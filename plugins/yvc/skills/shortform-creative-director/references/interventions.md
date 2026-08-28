# Intervention taxonomy, decision rules, scoring, output schema

Consult this once you've diagnosed the clip (`references/principles.md`)
and are choosing specific interventions for specific beats.

## Taxonomy

**Camera/framing** -- punch-in, punch-out, digital zoom, crop/reframe,
lateral movement, subtle push-in, reaction close-up, face tracking.

**Editing** -- hard cut, jump cut, cutaway, reaction shot, speed ramp,
freeze frame, brief pause, montage, visual callback.

**B-roll** -- contextual, literal, proof, environmental, archival,
product, location, reaction footage.

**Image** -- photograph, screenshot, article/social-post screenshot,
product image, portrait, chart, infographic.

**Generative visual** -- use only when the concept is genuinely hard to
source as real footage, a metaphor communicates faster than anything
literal would, the novelty is worth it, and the result does not contradict
the transcript's factual content.

**Text** -- emphasis text, key phrase, statistic, quote, question,
contradiction, payoff text, visual label.

**Motion graphics** -- arrows, circles, counters, diagrams, timelines,
charts, UI simulations, highlighted regions.

**Audio** -- impact, whoosh, riser, bass hit, silence, music change,
subtle ambience, transition sound.

**Pattern interrupts** -- sudden visual change, unexpected B-roll, abrupt
crop, silence, sound hit, fullscreen visual, meme/reaction, visual
contradiction, perspective change.

## B-roll decision rule

Insert B-roll when it performs a real function, not to fill a static
moment:

1. A concrete, nameable object is mentioned.
2. A specific person is mentioned.
3. A specific location is referenced.
4. A specific event is referenced.
5. A company or product is named.
6. A factual claim would benefit from visual proof.
7. A describable process is being narrated.
8. A visual metaphor genuinely beats the talking-head shot for this idea.
9. It functions as a real pattern interrupt at a beat transition.
10. It measurably raises emotional impact at this specific moment.

None of these firing is sufficient reason on its own to skip B-roll if the
moment calls for it, and none of them firing is reason to add it. Avoid:
generic stock footage, footage that's impressive but semantically
unrelated, footage that steps on an important line, repetition, and
B-roll whose only job is filling silence.

## Pattern interrupt rule

Before adding one, name what it's interrupting: a long static visual
state, an important reveal, an emotional escalation, a surprising
statement, a contradiction, a punchline, a key statistic, or a beat
transition. If there's no real pattern to break, an interrupt reads as
random rather than intentional -- skip it. Every interrupt must serve the
narrative; a zoom, meme, or sound hit added on a rhythm rather than a
reason is exactly the anti-pattern this rule exists to block.

## Hook-specific scoring

Evaluate the opening seconds independently on: clarity, curiosity, stakes,
novelty, visual strength, speed-to-value, emotional intensity, payoff
expectation. Route the result:

- Verbally strong, visually strong -> leave it, maybe note why it works.
- Verbally strong, visually weak -> add visual reinforcement (this is
  usually the single highest-value intervention available on a clip).
- Verbally weak -> check whether `clips.json`'s `text` contains a
  stronger later moment that could be pulled forward; never fabricate a
  stronger claim than the transcript supports.

## Open loops and payoff

Identify unanswered questions, promises, contradictions, mysteries, and
delayed reveals. Reinforce the tension visually (a brief pause, text
emphasis, a subtle zoom, a moment of reduced sound) rather than
illustrating the answer before the clip delivers it. If the narrative
depends on delayed payoff, an early illustrative visual undercuts the
exact mechanism making the clip work.

## Caption and audio discipline

Captions are both accessibility and a pacing tool: short phrase groups,
emphasis on words that carry meaning, readable typography, timing that
matches speech rhythm. Not a default: giant all-caps everywhere, rainbow
color, emoji spam, or word-by-word karaoke highlighting applied
uniformly. Emphasis is a tool for meaning, not decoration.

Audio interventions should be semantic and rare enough to still register:
an impact on a reveal, a whoosh on real movement, a bass hit on a major
transition, silence held before something important, a riser under
escalation. Audio that fires on every cut competes with the speaker
instead of supporting them.

## Scoring every candidate intervention

Score each intervention you seriously consider, 1-10 on each axis:

```
retention_impact | comprehension_impact | emotional_impact
novelty_impact    | narrative_fit         | distraction_risk
```

Weigh these into a priority (high / medium / low). Execute high-priority
interventions. Execute medium-priority ones only if they improve overall
rhythm without crowding the beat. Reject low-priority ones and say so --
prefer a handful of strong interventions over many weak ones; padding the
EDL to look thorough is a worse outcome than a short, confident list.

## Output schema

`work/<video_id>/creative_direction/<clip_id>.json`:

```json
{
  "clip_id": "c01",
  "creative_strategy": {
    "hook_type": "curiosity_gap",
    "archetype": "high_information_talking_head",
    "editing_style": "high_information_selective_intervention",
    "overall_intensity": 4
  },
  "interventions": [
    {
      "id": "edit_001",
      "start": 17.4,
      "end": 19.2,
      "type": "broll",
      "priority": "high",
      "query": "Tesla Model 3 exterior driving footage",
      "purpose": "visualize_concrete_subject",
      "reason": "The speaker names a specific, visually concrete object; the current frame is a static talking head doing no work for it.",
      "scores": {
        "retention_impact": 7, "comprehension_impact": 8,
        "emotional_impact": 4, "novelty_impact": 6,
        "narrative_fit": 8, "distraction_risk": 2
      },
      "expected_effect": ["comprehension", "novelty"]
    }
  ],
  "rejected_candidates": [
    {
      "start": 32.0, "end": 34.0, "type": "punch_in",
      "reason": "Speaker is already delivering the line with strong energy; a zoom here adds motion without adding meaning."
    }
  ]
}
```

Notes on the schema:

- `hook_type` comes from the upstream `clips.json` -- don't re-derive it.
- `archetype` is one of the five in `references/principles.md`; it should
  visibly explain `overall_intensity` (a Hormozi-style clip should almost
  never score above 4-5/10 on intensity; a MrBeast-style reveal can
  legitimately sit at 8-9).
- All timestamps are clip-relative seconds (0 = first frame of the clip),
  because that's what a downstream editing tool needs, not source-video
  time.
- `rejected_candidates` is optional but strongly preferred whenever you
  seriously weighed and passed on something -- it's what makes "we chose
  restraint" auditable instead of indistinguishable from "we didn't
  think of it."
- Adapt field names to a specific downstream tool's schema only if that
  tool's MCP is actually connected in-session (check via ToolSearch); the
  default is tool-agnostic semantic fields as shown above.

## Human-readable report

One block per intervention, in the exact format given in SKILL.md step 4.
If the clip earns zero interventions, the report is a single short
paragraph naming the archetype and explaining why no intervention cleared
the bar -- that is a complete, valid report, not a failure to find
anything.
