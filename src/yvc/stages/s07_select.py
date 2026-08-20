"""Clip selection: anchor each clip to the moment that made it interesting.

A scored segment is a topic block, typically 80-200 s. The interesting
claim lives at one specific instant inside it, and the clip is only worth
watching if it *opens* there.

The first version of this stage got that wrong. It generated every
contiguous sentence run that fitted the target duration and treated
"contains the hook" as a +5 bonus. Windows that omitted the hook entirely
could still win, and did: of five rendered clips, four opened on filler
("Kulturel bir sey degil ya o anlamda...") while the burned-in overlay
promised a line the audio never spoke. The failure was even recorded, as
a note, and then ignored.

So selection is now **hook-anchored**:

1. Locate the hook's real timestamp by matching the scored evidence quote
   against word-timed sentences. Matching is fuzzy on purpose -- the model
   paraphrases its own quote in 54% of segments, and an exact-match rule
   silently discards exactly the highest-scoring ones.
2. Start the clip at that sentence, allowing only a short lead-in.
3. Treat containing the hook as a hard constraint. A segment whose hook
   cannot be located produces no candidates at all, rather than a
   plausible-looking clip about nothing.

Non-overlap is enforced *within* each format via cardinality-constrained
interval scheduling. Across formats overlap is allowed: one genuinely
good moment can legitimately be both a Short and part of a LinkedIn cut.
That choice is recorded in the output rather than left implicit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from yvc.io import read_json, write_json


@dataclass
class Window:
    segment_id: str
    start: float
    end: float
    text: str
    score: float
    hook_type: str
    hook_line: str
    evidence_quote: str
    contains_evidence: bool
    overlap_fraction: float


@dataclass
class Clip:
    clip_id: str
    aspect: str
    start: float
    end: float
    duration: float
    score: float
    hook_type: str
    hook_line: str
    evidence_quote: str
    source_segment: str
    text: str
    selected_reason: str = "greedy"
    notes: list[str] = field(default_factory=list)


def _split_sentences(tokens: list[str]) -> list[list[int]]:
    """Group token indices into sentence-ish units on terminal punctuation."""
    groups: list[list[int]] = []
    buf: list[int] = []
    for i, token in enumerate(tokens):
        buf.append(i)
        if token.endswith((".", "?", "!", "…")):
            groups.append(buf)
            buf = []
    if buf:
        groups.append(buf)
    return groups


def flatten_words(transcript: dict) -> list[tuple[str, float, float]]:
    """All words of a transcript as (text, start, end), in order."""
    out: list[tuple[str, float, float]] = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words") or []:
            out.append((w["w"], float(w["start"]), float(w["end"])))
    return out


def sentences_from_words(
    words: list[tuple[str, float, float]], start: float, end: float
) -> list[tuple[str, float, float]]:
    """Sentence units carrying *real* word timings.

    Preferred over interpolation: a boundary derived from character counts
    can land mid-word, and a clip that opens half a syllable late loses
    exactly the hook it was selected for.
    """
    inside = [w for w in words if w[2] > start + 0.01 and w[1] < end - 0.01]
    if not inside:
        return []
    texts = [w[0] for w in inside]
    out: list[tuple[str, float, float]] = []
    for group in _split_sentences(texts):
        out.append(
            (
                " ".join(texts[i] for i in group),
                inside[group[0]][1],
                inside[group[-1]][2],
            )
        )
    return out


def _sentences(text: str, start: float, end: float) -> list[tuple[str, float, float]]:
    """Interpolated fallback for when word timings are unavailable.

    Used only if transcript.json is missing; boundaries are proportional to
    character count, which is approximate but keeps the stage runnable.
    """
    parts, buf = [], []
    for token in text.split():
        buf.append(token)
        if token.endswith((".", "?", "!", "…")):
            parts.append(" ".join(buf))
            buf = []
    if buf:
        parts.append(" ".join(buf))
    if not parts:
        return []

    total = sum(len(p) for p in parts) or 1
    span = end - start
    out, cursor = [], start
    for part in parts:
        share = span * len(part) / total
        out.append((part, cursor, cursor + share))
        cursor += share
    return out


def _norm_tokens(text: str) -> list[str]:
    """Diacritic- and case-folded tokens for matching.

    Folded because the model reproduces its quote from memory and drops
    diacritics as readily as it rewords; matching must survive both.
    """
    import re as _re

    from yvc.turkish.casing import ascii_fold, tr_lower

    return _re.findall(r"\w+", ascii_fold(tr_lower(text)), flags=_re.UNICODE)


def locate_hook(
    sentences: list[tuple[str, float, float]],
    evidence_quote: str,
    hook_line: str = "",
    *,
    min_confidence: float = 0.34,
) -> tuple[int, float]:
    """Index of the sentence where the hook starts, and confidence in it.

    Returns ``(-1, 0.0)`` when the hook cannot be found. That is a real
    answer: producing a clip whose opening has nothing to do with why the
    segment scored well is worse than producing no clip.

    Confidence is the best recall of the quote's content words over a
    short run of sentences, since a quoted claim often spans a sentence
    boundary. The evidence quote is authoritative; ``hook_line`` is the
    model's own paraphrase and is consulted only as a fallback.
    """
    if not sentences:
        return -1, 0.0

    for source in (evidence_quote, hook_line):
        wanted = set(_norm_tokens(source or ""))
        # Very short strings match too easily; a two-word "quote" would
        # anchor on any sentence containing a common word.
        if len(wanted) < 3:
            continue

        # Per-sentence recall first. Anchoring on the sentence that
        # actually carries the claim matters more than finding the region:
        # returning the start of a matching *run* put the claim two
        # sentences after the cut, so the clip opened on the run-up and
        # the overlay promised something not yet said.
        singles = [
            len(wanted & set(_norm_tokens(text))) / len(wanted)
            for text, _, _ in sentences
        ]
        best_index = max(range(len(singles)), key=singles.__getitem__)
        if singles[best_index] >= min_confidence:
            return best_index, round(singles[best_index], 3)

        # A claim split across sentences reaches the threshold only when
        # read together. Locate the run, then anchor on its densest
        # sentence rather than its first.
        best_start, best_score, best_len = -1, 0.0, 1
        for start in range(len(sentences)):
            seen: set[str] = set()
            for extra in range(3):
                if start + extra >= len(sentences):
                    break
                seen.update(_norm_tokens(sentences[start + extra][0]))
                recall = len(wanted & seen) / len(wanted)
                if recall > best_score:
                    best_start, best_score, best_len = start, recall, extra + 1

        if best_score >= min_confidence:
            span = range(best_start, best_start + best_len)
            densest = max(span, key=lambda i: singles[i])
            return densest, round(best_score, 3)

    return -1, 0.0


def overlay_matches_opening(hook_line: str, opening_text: str) -> bool:
    """Does the burned-in overlay describe what is actually said first?

    ``hook_line`` is the model's punchy summary of the *segment*, and a
    segment holds several claims. Burning it over a clip that opens on a
    different one promises the viewer something the audio never delivers
    -- the precise complaint that sent this stage back for rework. When
    they disagree the overlay is dropped rather than shown, because no
    caption beats a false one.
    """
    promised = set(_norm_tokens(hook_line))
    # Turkish function words carry no topical commitment.
    promised -= {
        "bir", "bu", "o", "ve", "ama", "da", "de", "mi", "mu", "ne", "icin",
        "daha", "cok", "gibi", "ile", "ya", "en", "her", "yok", "var",
    }
    if len(promised) < 2:
        return False
    spoken = set(_norm_tokens(opening_text))
    return len(promised & spoken) / len(promised) >= 0.4


def hook_anchored_windows(
    scored: dict,
    min_s: float,
    max_s: float,
    *,
    sentences: list[tuple[str, float, float]],
    lead_in_s: float = 2.5,
) -> list[Window]:
    """Candidate windows that all *open* on the hook.

    Only the end is free to vary. The start is the hook sentence, or one
    sentence earlier when that costs less than `lead_in_s` -- a brief
    run-up can make a claim land better, but a long one buries it past the
    three seconds the viewer actually gives us.
    """
    index, confidence = locate_hook(
        sentences, scored.get("evidence_quote", ""), scored.get("hook_line", "")
    )
    if index < 0:
        return []

    starts = [index]
    if index > 0 and sentences[index][1] - sentences[index - 1][1] <= lead_in_s:
        starts.append(index - 1)

    parent_span = max(1e-6, scored["end"] - scored["start"])
    windows: list[Window] = []

    for start_index in starts:
        begin = sentences[start_index][1]
        for end_index in range(start_index, len(sentences)):
            finish = sentences[end_index][2]
            span = finish - begin
            if span < min_s:
                continue
            if span > max_s:
                break

            text = " ".join(s[0] for s in sentences[start_index : end_index + 1])
            fraction = min(1.0, span / parent_span)

            # The hook is guaranteed present, so the old +5 bonus is gone.
            # What varies now is how well it was located and how much of
            # the parent's substance the window keeps.
            score = scored["total"] * (fraction**0.5) * (0.75 + 0.25 * confidence)
            if start_index == index:
                score += 3.0  # opening exactly on the hook beats a run-up

            windows.append(
                Window(
                    segment_id=scored["segment_id"],
                    start=round(begin, 3),
                    end=round(finish, 3),
                    text=text,
                    score=round(score, 2),
                    hook_type=scored.get("hook_type", ""),
                    hook_line=scored.get("hook_line", ""),
                    evidence_quote=(scored.get("evidence_quote") or "").strip(),
                    contains_evidence=True,
                    overlap_fraction=round(fraction, 3),
                )
            )
    return windows


def candidate_windows(
    scored: dict,
    min_s: float,
    max_s: float,
    *,
    sentences: list[tuple[str, float, float]] | None = None,
) -> list[Window]:
    """All contiguous sentence runs of a segment that fit the duration band.

    `sentences` is supplied by the caller when word timings are available.
    Falling back to `scored["text"]` keeps the function usable standalone.
    """
    if sentences is None:
        sentences = _sentences(scored["text"], scored["start"], scored["end"])
    if not sentences:
        return []

    parent_span = scored["end"] - scored["start"]
    evidence = (scored.get("evidence_quote") or "").strip()
    windows: list[Window] = []

    for i in range(len(sentences)):
        for j in range(i, len(sentences)):
            begin = sentences[i][1]
            finish = sentences[j][2]
            span = finish - begin
            if span < min_s:
                continue
            if span > max_s:
                break

            text = " ".join(s[0] for s in sentences[i : j + 1])
            fraction = span / parent_span if parent_span > 0 else 1.0
            has_evidence = bool(evidence) and evidence[:40] in text

            score = scored["total"] * (fraction ** 0.5)
            if has_evidence:
                score += 5.0

            windows.append(
                Window(
                    segment_id=scored["segment_id"],
                    start=round(begin, 3),
                    end=round(finish, 3),
                    text=text,
                    score=round(score, 2),
                    hook_type=scored.get("hook_type", ""),
                    hook_line=scored.get("hook_line", ""),
                    evidence_quote=evidence,
                    contains_evidence=has_evidence,
                    overlap_fraction=round(fraction, 3),
                )
            )
    return windows


def schedule_non_overlapping(
    windows: list[Window], count: int, *, min_gap_s: float
) -> list[Window]:
    """Best `count` non-overlapping windows, by cardinality-constrained DP.

    The cardinality constraint is the whole point. Maximising total score
    over *any* number of intervals and then truncating rewards packing:
    two mediocre 21 s windows (43.0 + 38.3) beat one excellent 29 s window
    (62.9), so the scheduler fills the quota with filler and drops the
    clip the hook engine actually liked. Observed on real data, not
    hypothetical.

    So the state carries how many intervals have been taken:
    ``best[i][k]`` = best achievable score using the first `i` windows
    (sorted by end time) while taking exactly `k` of them. Optimal, and
    it cannot be beaten by a greedy pass.
    """
    if not windows or count <= 0:
        return []

    ordered = sorted(windows, key=lambda w: w.end)
    n = len(ordered)

    # p[i] = index of the last window ending at least min_gap before i starts
    p = [-1] * n
    for i, w in enumerate(ordered):
        lo, hi, best = 0, i - 1, -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if ordered[mid].end + min_gap_s <= w.start:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        p[i] = best

    NEG = float("-inf")
    # best[i][k]; row 0 = no windows considered.
    best = [[NEG] * (count + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        best[i][0] = 0.0

    for i in range(1, n + 1):
        for k in range(1, count + 1):
            skip = best[i - 1][k]
            prior = best[p[i - 1] + 1][k - 1]
            take = ordered[i - 1].score + prior if prior > NEG else NEG
            best[i][k] = max(skip, take)

    # Take the largest k that is actually achievable -- fewer windows than
    # requested is a legitimate outcome and must not be a crash.
    k = max((j for j in range(count + 1) if best[n][j] > NEG), default=0)

    chosen: list[Window] = []
    i = n
    while i > 0 and k > 0:
        prior = best[p[i - 1] + 1][k - 1]
        take = ordered[i - 1].score + prior if prior > NEG else NEG
        if take >= best[i - 1][k]:
            chosen.append(ordered[i - 1])
            i = p[i - 1] + 1
            k -= 1
        else:
            i -= 1

    chosen.sort(key=lambda w: w.score, reverse=True)
    return chosen


def top_hook_types(priors, limit: int = 2) -> set[str]:
    """The hook types currently being *favoured*, if any.

    Only types the loop has actually learned to prefer count. Ranking by
    multiplier alone is wrong when nothing has been measured: every
    multiplier is then exactly 1.0, ``sorted`` still returns a top two,
    and the quota starts displacing high-scoring clips to "explore"
    alternatives to a preference that does not exist. Observed doing
    precisely that -- it swapped a 60.9 clip set for one containing a
    31.1.
    """
    if priors is None or not getattr(priors, "priors", None):
        return set()
    favoured = [p for p in priors.priors.values() if p.multiplier > 1.0]
    if not favoured:
        return set()
    favoured.sort(key=lambda p: p.multiplier, reverse=True)
    return {p.hook_type for p in favoured[:limit]}


def conflicts(window: Window, chosen: list[Window], min_gap_s: float) -> bool:
    return any(
        window.start < other.end + min_gap_s
        and other.start < window.end + min_gap_s
        for other in chosen
    )


def exploration_quota(count: int, ratio: float = 0.20) -> int:
    """How many of `count` slots must go to non-exploited hook types."""
    if count <= 0:
        return 0
    return max(1, math.ceil(ratio * count))


def with_exploration(
    pool: list[Window],
    picked: list[Window],
    *,
    count: int,
    min_gap_s: float,
    exploited: set[str],
    ratio: float = 0.20,
) -> tuple[list[Window], list[Window]]:
    """Reserve slots for hook types outside the current top two.

    Bounds and Thompson sampling both damp runaway convergence but
    neither prevents it: a type that never gets posted never accumulates
    evidence, so its multiplier decays toward neutral rather than being
    disproved, and the exploited types keep winning on score alone. This
    quota is the guard that actually forces the comparison to happen.

    Returns (selection, explored). The selection is rebuilt through the
    same scheduler rather than swapped by hand, because swapping picked
    windows silently breaks the non-overlap and min_gap guarantees the
    scheduler exists to provide.
    """
    if not exploited or count <= 0:
        return picked, []

    need = exploration_quota(count, ratio)
    already = [w for w in picked if w.hook_type not in exploited]
    if len(already) >= need:
        return picked, []

    explore_pool = [w for w in pool if w.hook_type not in exploited]
    if not explore_pool:
        return picked, []

    explored = schedule_non_overlapping(explore_pool, need, min_gap_s=min_gap_s)
    if not explored:
        return picked, []

    remaining = [w for w in pool if not conflicts(w, explored, min_gap_s)]
    filler = schedule_non_overlapping(
        remaining, count - len(explored), min_gap_s=min_gap_s
    )
    selection = sorted(explored + filler, key=lambda w: w.start)
    return selection, explored


def select(
    scores_path: str | Path,
    out_path: str | Path,
    *,
    segments_path: str | Path | None = None,
    transcript_path: str | Path | None = None,
    vertical: dict | None = None,
    horizontal: dict | None = None,
    threshold: float = 55.0,
    relax_step: float = 5.0,
    relax_floor: float = 40.0,
    min_gap_s: float = 5.0,
    cross_format_overlap: bool = True,
    priors=None,
    exploration_ratio: float = 0.20,
) -> dict:
    """Choose clips for each format and write clips.json."""
    vertical = vertical or {"count": 3, "min_s": 20, "max_s": 60}
    horizontal = horizontal or {"count": 2, "min_s": 60, "max_s": 120}

    data = read_json(scores_path)
    segments = data["segments"]

    # scores.json records judgements, not prose -- the text lives in
    # segments.json and the word timings in transcript.json. Joining here
    # keeps each artifact single-purpose instead of duplicating the
    # transcript into every downstream file.
    if segments_path and Path(segments_path).exists():
        text_by_id = {
            s["id"]: s.get("text", "")
            for s in read_json(segments_path).get("segments", [])
        }
        for seg in segments:
            seg.setdefault("text", text_by_id.get(seg["segment_id"], ""))

    words: list[tuple[str, float, float]] = []
    if transcript_path and Path(transcript_path).exists():
        words = flatten_words(read_json(transcript_path))
    if words:
        print(f"[select] using real word timings ({len(words)} words)")
    else:
        print("[select] WARNING no transcript: falling back to interpolated timings")

    unlocatable: list[str] = []

    def _windows(seg: dict, min_s: float, max_s: float) -> list[Window]:
        units = (
            sentences_from_words(words, seg["start"], seg["end"]) if words else None
        )
        if units:
            anchored = hook_anchored_windows(seg, min_s, max_s, sentences=units)
            if anchored:
                return anchored
            # Produce nothing rather than a clip that opens on filler while
            # the overlay promises a claim. Distinguish the two reasons: a
            # segment too short for this format is entirely normal, an
            # unfindable hook is a scoring problem worth seeing.
            index, _ = locate_hook(
                units, seg.get("evidence_quote", ""), seg.get("hook_line", "")
            )
            if index < 0 and seg["segment_id"] not in unlocatable:
                unlocatable.append(seg["segment_id"])
            return []
        return candidate_windows(seg, min_s, max_s, sentences=units)

    clips: list[Clip] = []
    relaxations: dict[str, float] = {}

    for aspect, spec in (("9:16", vertical), ("16:9", horizontal)):
        active = threshold
        picked: list[Window] = []

        while True:
            eligible = [s for s in segments if s["total"] >= active]
            pool: list[Window] = []
            for seg in eligible:
                pool.extend(_windows(seg, spec["min_s"], spec["max_s"]))

            picked = schedule_non_overlapping(pool, spec["count"], min_gap_s=min_gap_s)
            if len(picked) >= spec["count"] or active <= relax_floor:
                break
            # A no-touch pipeline must fill its quota -- but it must also
            # say when it lowered the bar to do so.
            active -= relax_step
            relaxations[aspect] = active

        exploited = top_hook_types(priors)
        picked, explored = with_exploration(
            pool, picked, count=spec["count"], min_gap_s=min_gap_s,
            exploited=exploited, ratio=exploration_ratio,
        )
        explored_keys = {(w.segment_id, w.start, w.end) for w in explored}
        if explored:
            print(
                f"[select] {aspect}: {len(explored)} slot(s) reserved for "
                f"exploration outside {sorted(exploited)}"
            )

        for window in picked:
            # The overlay is only shown when the opening actually says it.
            # Roughly the first three seconds, which is all the viewer gives
            # us and exactly the span the overlay is claiming to describe.
            opening = " ".join(window.text.split()[:14])
            honest_overlay = overlay_matches_opening(window.hook_line, opening)

            notes: list[str] = []
            if not window.contains_evidence:
                notes.append("window does not contain the scored evidence quote")
            if not honest_overlay and window.hook_line:
                notes.append(
                    f"overlay suppressed: {window.hook_line!r} is not what the "
                    "clip opens with"
                )

            clips.append(
                Clip(
                    clip_id=f"c{len(clips) + 1:02d}",
                    aspect=aspect,
                    start=window.start,
                    end=window.end,
                    duration=round(window.end - window.start, 2),
                    score=window.score,
                    hook_type=window.hook_type,
                    hook_line=window.hook_line if honest_overlay else "",
                    evidence_quote=window.evidence_quote,
                    source_segment=window.segment_id,
                    text=window.text,
                    selected_reason=(
                        "exploration_quota"
                        if (window.segment_id, window.start, window.end)
                        in explored_keys else "greedy"
                    ),
                    notes=notes,
                )
            )

    payload = {
        "cross_format_overlap": cross_format_overlap,
        "threshold": threshold,
        "relaxed_threshold": relaxations or None,
        "counts": {
            "9:16": sum(1 for c in clips if c.aspect == "9:16"),
            "16:9": sum(1 for c in clips if c.aspect == "16:9"),
        },
        "clips": [c.__dict__ for c in clips],
    }
    write_json(out_path, payload)

    for clip in clips:
        print(
            f"[select] {clip.clip_id} {clip.aspect} "
            f"{clip.start:7.1f}-{clip.end:7.1f}s ({clip.duration:5.1f}s) "
            f"score={clip.score:5.1f} type={clip.hook_type}"
        )
    if unlocatable:
        print(
            f"[select] {len(unlocatable)} segment(s) dropped -- hook not locatable "
            f"in the transcript: {', '.join(unlocatable[:8])}"
        )
    if relaxations:
        print(f"[select] WARNING threshold relaxed to meet quota: {relaxations}")
    print(f"[select] {len(clips)} clips -> {out_path}")
    return payload


if __name__ == "__main__":
    import sys

    import yvc.bootstrap  # noqa: F401

    base = Path(sys.argv[1] if len(sys.argv) > 1 else "work/r39OrneyMDs")
    select(
        base / "scores.json",
        base / "clips.json",
        segments_path=base / "segments.json",
        transcript_path=base / "transcript.json",
    )
