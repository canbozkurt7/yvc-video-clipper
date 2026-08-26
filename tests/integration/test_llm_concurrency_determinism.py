"""Concurrency must not be observable in the artifacts.

Running the LLM stages in parallel is only safe if the output does not
depend on which call finishes first, and two merges in this pipeline are
order-sensitive by design:

  * segmentation keeps the *first* title offered for a boundary
    (``chosen.setdefault``), and overlapping windows routinely offer the
    same boundary twice;
  * scoring sorts by total with a stable sort, so segments that tie are
    left in insertion order.

Both would silently start varying between identical runs if results were
merged as they arrived. A real run cannot prove this -- the `claude` CLI
fails a window occasionally, and an uncached retry changes the artifact
for reasons that have nothing to do with threading -- so the engine is
stubbed here and the delays are inverted to guarantee that completion
order is the reverse of input order.
"""

from __future__ import annotations

import json
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from yvc.llm.claude_cli import LLMError
from yvc.stages.s05_segment import segment_transcript
from yvc.stages.s06_score import LLMScores, score_segments

CONCURRENCIES = [1, 4]


class _Result:
    """Enough of LLMResult for the stages: data plus the cache flag."""

    def __init__(self, data):
        self.data = data
        self.cache_hit = False


class _SegmentStub:
    """Answers each window with the candidates that window was shown.

    Titles are tagged with the window that produced them, which is what
    makes an ordering mistake visible: adjacent windows overlap, so the
    same boundary id arrives from two windows with two different titles
    and only the lower window index may win.
    """

    def __init__(self, concurrency: int) -> None:
        self.concurrency = concurrency

    def complete(self, task, prompt, schema, model=None, **kwargs):
        window = int(task.split(".w")[1])
        # Later windows answer sooner, so completion order inverts.
        time.sleep(0.02 * max(0, 8 - window))
        ids = _candidate_ids(prompt)[:4]
        return _Result(
            schema(boundaries=[{"id": i, "title": f"w{window}-b{i}"} for i in ids])
        )


class _FlakySegmentStub(_SegmentStub):
    """Fails a chosen set of windows, earliest-failing last."""

    def __init__(self, concurrency: int, failing: set[int]) -> None:
        super().__init__(concurrency)
        self.failing = failing

    def complete(self, task, prompt, schema, model=None, **kwargs):
        window = int(task.split(".w")[1])
        if window in self.failing:
            # The lower window fails slowest, so failures *arrive*
            # high-first. The pool must still record them low-first.
            time.sleep(0.02 * (10 - window))
            raise LLMError(f"stubbed failure in window {window}")
        return super().complete(task, prompt, schema, model=model, **kwargs)


class _ScoreStub:
    """One fixed judgement, so every segment ties on total.

    A tie is the only condition under which the stable sort can expose
    merge order, so the stub manufactures ties on purpose.
    """

    def __init__(self, concurrency: int) -> None:
        self.concurrency = concurrency

    def complete(self, task, prompt, schema, model=None, **kwargs):
        index = int(task.split("seg_")[1])
        time.sleep(0.02 * max(0, 8 - index))
        return _Result(LLMScores(
            hook_3s=8, curiosity_gap=7, emotional_charge=6, standalone=7,
            audience_fit=8, hook_type="contrarian", hook_line="kanca",
            evidence_quote="bu bir test alintisidir ve yeterince uzundur",
            rationale="stub",
        ))


def _candidate_ids(prompt: str) -> list[int]:
    """Pull the candidate ids back out of the rendered prompt."""
    ids = []
    for line in prompt.splitlines():
        head = line.split("\t", 1)[0].strip()
        if head.isdigit():
            ids.append(int(head))
    return ids


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    """A transcript long enough to span several overlapping windows."""
    words = []
    t = 0.0
    for i in range(1200):
        token = "kelime" + ("." if i % 9 == 8 else "")
        words.append({"w": token, "start": round(t, 3), "end": round(t + 0.4, 3)})
        # A gap every so often adds pause-derived boundaries too.
        t += 0.5 if i % 9 else 1.4
    path = tmp_path / "transcript.json"
    path.write_text(
        json.dumps({
            "segments": [{"start": 0.0, "end": words[-1]["end"], "words": words}],
            "duration": words[-1]["end"],
        }),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def scoring_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """Identical segments over an exactly periodic tone.

    Identical text and second-aligned windows of equal length over a
    waveform that repeats every second means the deterministic signals
    come out bit-for-bit equal, so the totals genuinely tie.
    """
    text = (
        "Bu bir test alintisidir ve yeterince uzundur diye dusunuyorum. "
        "Ucret artislari yuzde 40 seviyesinde seyrediyor bu yil boyunca. "
        "Peki bu rakam neden bu kadar yuksek sizce acaba dostlar? "
        "Sirketlerin bordro yuku her gecen ay artmaya devam ediyor."
    )
    segments = {"segments": [
        {"id": f"seg_{i:03d}", "start": float(i * 60), "end": float(i * 60 + 30),
         "text": text, "speakers": ["A", "B"]}
        for i in range(8)
    ]}
    segments_path = tmp_path / "segments.json"
    segments_path.write_text(json.dumps(segments), encoding="utf-8")

    rate = 16000
    one_second = (np.sin(np.arange(rate) * 0.05) * 6000).astype("<i2")
    tone = np.tile(one_second, 600)
    wav_path = tmp_path / "audio16k_raw.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(tone.tobytes())

    return segments_path, wav_path


def test_segmentation_is_identical_at_every_concurrency(transcript, tmp_path):
    """Overlapping windows offer the same boundary under two titles.

    This one cannot currently fail, and that is worth stating: the
    contested title ``chosen.setdefault`` protects is discarded by
    ``_materialize``, which hardcodes ``title=""``, and the surviving ids
    are re-sorted by timestamp. So segmentation is order-insensitive by
    accident rather than by design. The test is kept as the regression
    net for the day the titles are actually used -- at which point the
    ordered merge stops being belt-and-braces and starts being load
    bearing. ``test_failed_windows_are_recorded_in_window_order`` is the
    one that bites today.
    """
    outputs = []
    for concurrency in CONCURRENCIES:
        out = tmp_path / f"segments_{concurrency}.json"
        segment_transcript(
            transcript, out,
            llm=_SegmentStub(concurrency), model=None,
            window_s=120.0, overlap_s=30.0, min_segment_s=10.0,
        )
        outputs.append(out.read_text(encoding="utf-8"))

    assert outputs[0] == outputs[1]


def test_failed_windows_are_recorded_in_window_order(transcript, tmp_path):
    """The one ordering in segments.json a reader can actually see.

    ``failed_windows`` is a list in the payload, and the windows that
    fail are exactly the ones whose completion order is least
    predictable, so it is the sharpest observable check that the merge
    still runs in window order rather than arrival order.
    """
    out = tmp_path / "segments.json"
    payload = segment_transcript(
        transcript, out,
        # Wide enough that every window starts at once -- with a narrow
        # pool the later window cannot start until an earlier slot frees,
        # which would hide an arrival-ordered merge behind the schedule.
        llm=_FlakySegmentStub(16, failing={1, 4}), model=None,
        window_s=120.0, overlap_s=30.0, min_segment_s=10.0,
    )
    recorded = [f["window"] for f in payload["failed_windows"]]
    assert recorded == sorted(recorded), recorded
    assert set(recorded) == {1, 4}


def test_scoring_is_identical_at_every_concurrency(scoring_inputs, tmp_path):
    """All eight segments tie, so only insertion order can break them."""
    segments_path, wav_path = scoring_inputs
    outputs = []
    for concurrency in CONCURRENCIES:
        out = tmp_path / f"scores_{concurrency}.json"
        payload = score_segments(
            segments_path, wav_path, out,
            llm=_ScoreStub(concurrency), model=None,
        )
        totals = {s["total"] for s in payload["segments"]}
        assert len(totals) == 1, f"fixture failed to produce ties: {totals}"
        outputs.append(out.read_text(encoding="utf-8"))

    assert outputs[0] == outputs[1]


def test_tied_segments_stay_in_segment_order(scoring_inputs, tmp_path):
    """The tie-break the stable sort is relied on to provide."""
    segments_path, wav_path = scoring_inputs
    payload = score_segments(
        segments_path, wav_path, tmp_path / "scores.json",
        llm=_ScoreStub(4), model=None,
    )
    ids = [s["segment_id"] for s in payload["segments"]]
    assert ids == sorted(ids)
