"""Semantic segmentation of the transcript into topical units.

The model never emits a timestamp. Boundaries are enumerated
deterministically from word timings and punctuation, and the LLM returns
only their integer ids. A hallucinated id fails validation loudly; a
hallucinated *timestamp* would silently land mid-word, which is exactly
the failure the brief forbids.

The transcript is processed in overlapping windows rather than one call.
A 60-minute Turkish conversation is ~20k tokens, which fits, but a single
call is all-or-nothing on a malformed response and models attend poorly
to the middle of a long document. Eight windows with 90 s of shared
context degrade gracefully: one bad window is retried alone, and if it
still fails only that stretch falls back to pause-based splitting.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from yvc.io import read_json, write_json
from yvc.llm.claude_cli import ClaudeCLI, LLMError, LLMResult
from yvc.llm.guard import require_success_ratio
from yvc.llm.pool import concurrency_of, map_ordered
from yvc.signals.text import sentence_boundaries


class _Boundary(BaseModel):
    id: int
    title: str = Field(default="", max_length=120)


class _WindowResult(BaseModel):
    """What the model returns for one window: ids, nothing more."""

    boundaries: list[_Boundary]


@dataclass
class _WindowOutcome:
    """What one window contributed, carried back to an ordered merge.

    Both fields empty means the window was skipped for having too few
    candidates -- a real outcome, distinct from a failure, and the reason
    this is not just an optional result.
    """

    result: LLMResult | None = None
    failure: dict | None = None


@dataclass
class Segment:
    id: str
    start: float
    end: float
    title: str
    text: str
    word_count: int
    start_boundary_id: int | None
    end_boundary_id: int | None


PROMPT = """Aşağıda bir Türkçe panel/podcast kaydının bir bölümü var. Kayıt
maaş, bordro, zam ve İK konularını tartışıyor.

Metin, numaralandırılmış CÜMLE SINIRI adaylarına bölünmüş durumda. Her aday
bir `id` ve o noktada başlayan cümleyi taşıyor.

GÖREVİN: Bu bölümü anlamsal olarak tutarlı konu bloklarına ayır. Her bloğun
BAŞLADIĞI aday sınırın `id`sini döndür.

KURALLAR:
- Yalnızca aşağıdaki listede GERÇEKTEN VAR OLAN id'leri kullan. Yeni id uydurma.
- Zaman damgası ÜRETME. Sadece id döndür.
- Bir konu bloğu tek bir fikri baştan sona kapsamalı; cümlenin ortasında bölme.
- {context_note}
- Her blok için kısa (en fazla 8 kelime) Türkçe bir başlık yaz.
- Bu bölümde tipik olarak {expected} blok beklenir.

ADAY SINIRLAR:
{candidates}
"""


def _window_candidates(
    boundaries: list[dict], words: list[dict], start_t: float, end_t: float
) -> list[dict]:
    return [b for b in boundaries if start_t <= b["t"] < end_t]


def _render_candidates(
    candidates: list[dict],
    words: list[dict],
    core_start: float,
    core_end: float,
    max_chars: int = 160,
) -> str:
    """One line per candidate: id, time, and the sentence starting there.

    Candidates inside the overlap are marked so the model knows they are
    context rather than territory it should carve up.
    """
    lines = []
    for index, cand in enumerate(candidates):
        begin = cand["word_index"]
        stop = (
            candidates[index + 1]["word_index"]
            if index + 1 < len(candidates)
            else min(begin + 60, len(words))
        )
        text = " ".join(w["w"] for w in words[begin:stop])[:max_chars]
        tag = "" if core_start <= cand["t"] < core_end else "  [BAĞLAM]"
        lines.append(f'{cand["id"]}\t{cand["t"]:.1f}s\t{text}{tag}')
    return "\n".join(lines)


def segment_transcript(
    transcript_path: str | Path,
    out_path: str | Path,
    *,
    llm: ClaudeCLI | None = None,
    window_s: float = 480.0,
    overlap_s: float = 90.0,
    min_segment_s: float = 25.0,
    max_segment_s: float = 300.0,
    gap_s: float = 0.65,
    model: str | None = "sonnet",
    min_success_ratio: float = 0.6,
) -> dict:
    """Segment a transcript and write segments.json."""
    data = read_json(transcript_path)
    segments_in = data["segments"]
    duration = float(data.get("duration") or segments_in[-1]["end"])

    words: list[dict] = []
    for seg in segments_in:
        words.extend(seg.get("words") or [])
    if not words:
        raise ValueError("transcript has no word-level timings")

    boundaries = sentence_boundaries(words, gap_s=gap_s)
    valid_ids = {b["id"] for b in boundaries}
    by_id = {b["id"]: b for b in boundaries}
    print(f"[segment] {len(words)} words -> {len(boundaries)} candidate boundaries")

    llm = llm or ClaudeCLI()

    chosen: dict[int, str] = {}
    votes: dict[int, int] = {}
    failed_windows: list[dict] = []

    starts = []
    cursor = 0.0
    while cursor < duration:
        starts.append(cursor)
        cursor += window_s

    def _run_window(index: int, core_start: float) -> _WindowOutcome:
        """One window's LLM call. Touches no shared state.

        Everything read here -- boundaries, words -- is read-only, and
        the merge is deliberately left to the caller so that arrival
        order cannot reach segments.json.
        """
        core_end = min(core_start + window_s, duration)
        lo = max(0.0, core_start - overlap_s)
        hi = min(duration, core_end + overlap_s)

        candidates = _window_candidates(boundaries, words, lo, hi)
        if len(candidates) < 3:
            return _WindowOutcome()

        expected = max(2, int((core_end - core_start) / 90))
        prompt = PROMPT.format(
            context_note=(
                "[BAĞLAM] etiketli adaylar sadece bağlam içindir; oraya blok "
                "başlangıcı koyma (çok güçlü bir gerekçe yoksa)."
            ),
            expected=expected,
            candidates=_render_candidates(candidates, words, core_start, core_end),
        )

        try:
            result = llm.complete(
                f"segment.w{index}", prompt, _WindowResult, model=model
            )
        except LLMError as exc:
            print(f"[segment] window {index} failed: {exc}")
            return _WindowOutcome(failure={"window": index, "error": str(exc)[:200]})

        print(
            f"[segment] window {index} ({core_start:.0f}-{core_end:.0f}s): "
            f"{len(result.data.boundaries)} boundaries"
            f"{' [cached]' if result.cache_hit else ''}"
        )
        return _WindowOutcome(result=result)

    # Windows are independent, so they overlap; the merge below does not.
    outcomes = map_ordered(_run_window, starts, concurrency_of(llm))

    for outcome in outcomes:
        if outcome.failure is not None:
            failed_windows.append(outcome.failure)
            continue
        if outcome.result is None:
            continue
        for item in outcome.result.data.boundaries:
            # An id outside the candidate set means the model invented it.
            # That invalidates the item, not the run.
            if item.id not in valid_ids:
                continue
            votes[item.id] = votes.get(item.id, 0) + 1
            chosen.setdefault(item.id, item.title)

    # A window that fails falls back to pause splitting below, which is
    # a fair trade for one window and meaningless for most of them.
    require_success_ratio(
        "segment",
        sum(1 for o in outcomes if o.result is not None),
        sum(1 for o in outcomes if o.result is not None or o.failure is not None),
        min_success_ratio,
    )

    # Windows that failed entirely still need boundaries, or their stretch
    # would collapse into one enormous segment. Fall back to the largest
    # pauses, which is worse than the model but never absent.
    if failed_windows:
        _fill_from_pauses(
            boundaries, chosen, votes, failed_windows, starts, window_s, duration
        )

    picked = sorted(chosen.keys(), key=lambda i: by_id[i]["t"])
    picked = _dedupe(picked, by_id, min_gap_s=5.0, votes=votes)
    segments = _materialize(
        picked, by_id, words, duration, min_segment_s, max_segment_s
    )

    payload = {
        "method": "llm_windowed_v1",
        "window_s": window_s,
        "overlap_s": overlap_s,
        "candidate_count": len(boundaries),
        "failed_windows": failed_windows,
        "segments": [s.__dict__ for s in segments],
    }
    write_json(out_path, payload)
    print(f"[segment] {len(segments)} segments -> {out_path}")
    return payload


def _fill_from_pauses(boundaries, chosen, votes, failed, starts, window_s, duration):
    """Deterministic backstop for a window the model could not handle."""
    for entry in failed:
        index = entry["window"]
        lo = starts[index]
        hi = min(lo + window_s, duration)
        inside = [b for b in boundaries if lo <= b["t"] < hi and b["reason"] == "pause"]
        # One boundary roughly every 90 seconds.
        step = max(1, len(inside) // max(1, int((hi - lo) / 90)))
        for b in inside[::step]:
            chosen.setdefault(b["id"], "")
            votes[b["id"]] = votes.get(b["id"], 0)
        entry["fallback"] = "pause_split"


def _dedupe(picked: list[int], by_id: dict, *, min_gap_s: float, votes: dict) -> list[int]:
    """Collapse boundaries that sit within min_gap_s, keeping the best-supported."""
    out: list[int] = []
    for bid in picked:
        if out and by_id[bid]["t"] - by_id[out[-1]]["t"] < min_gap_s:
            if votes.get(bid, 0) > votes.get(out[-1], 0):
                out[-1] = bid
            continue
        out.append(bid)
    return out


def _materialize(
    picked: list[int],
    by_id: dict,
    words: list[dict],
    duration: float,
    min_s: float,
    max_s: float,
) -> list[Segment]:
    """Turn boundary ids into segments tiling [0, duration] with no gaps."""
    # Start at the first word, not at 0.0. A transcript does not
    # necessarily begin at the origin -- a resumed or sliced one starts
    # wherever its audio starts -- and assuming zero fabricates a segment
    # spanning empty time before the first word.
    origin = words[0].get("start", 0.0) if words else 0.0
    edges = [origin] + [by_id[i]["t"] for i in picked] + [duration]
    idx_edges = [0] + [by_id[i]["word_index"] for i in picked] + [len(words)]

    segments: list[Segment] = []
    for n in range(len(edges) - 1):
        start, end = edges[n], edges[n + 1]
        if end - start < 1.0:
            continue
        # A long span holding almost no words is an artifact of a gap in
        # the transcript, not a topic. Scoring it wastes an LLM call and
        # pollutes the ranking.
        if (end - start) > 20.0 and (idx_edges[n + 1] - idx_edges[n]) < 15:
            continue
        w0, w1 = idx_edges[n], idx_edges[n + 1]
        text = " ".join(w["w"] for w in words[w0:w1]).strip()
        segments.append(
            Segment(
                id=f"seg_{len(segments):03d}",
                start=round(start, 3),
                end=round(end, 3),
                title="",
                text=text,
                word_count=w1 - w0,
                start_boundary_id=picked[n - 1] if n > 0 else None,
                end_boundary_id=picked[n] if n < len(picked) else None,
            )
        )

    segments = _merge_short(segments, min_s)
    return _split_long(segments, max_s)


def _merge_short(segments: list[Segment], min_s: float) -> list[Segment]:
    out: list[Segment] = []
    for seg in segments:
        if out and (seg.end - seg.start) < min_s:
            prev = out[-1]
            prev.end = seg.end
            prev.text = f"{prev.text} {seg.text}".strip()
            prev.word_count += seg.word_count
            continue
        out.append(seg)
    for n, seg in enumerate(out):
        seg.id = f"seg_{n:03d}"
    return out


def _split_long(segments: list[Segment], max_s: float) -> list[Segment]:
    """Halve anything over max_s. Crude, but only reached when the model
    produced no interior boundary at all for a long stretch."""
    out: list[Segment] = []
    for seg in segments:
        span = seg.end - seg.start
        if span <= max_s:
            out.append(seg)
            continue
        pieces = int(span // max_s) + 1
        step = span / pieces
        words_per = max(1, seg.word_count // pieces)
        tokens = seg.text.split()
        for k in range(pieces):
            out.append(
                Segment(
                    id=seg.id,
                    start=round(seg.start + k * step, 3),
                    end=round(min(seg.end, seg.start + (k + 1) * step), 3),
                    title=seg.title,
                    text=" ".join(tokens[k * words_per : (k + 1) * words_per]),
                    word_count=words_per,
                    start_boundary_id=None,
                    end_boundary_id=None,
                )
            )
    for n, seg in enumerate(out):
        seg.id = f"seg_{n:03d}"
    return out


if __name__ == "__main__":
    import sys

    import yvc.bootstrap  # noqa: F401

    src = sys.argv[1] if len(sys.argv) > 1 else "work/r39OrneyMDs/transcript.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "work/r39OrneyMDs/segments.json"
    segment_transcript(src, dst)
