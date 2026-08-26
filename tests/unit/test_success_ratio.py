"""A run that could not reach the model must not look like a clean one.

The incident this encodes: the `claude` CLI stopped answering partway
through a real run, all 26 segments fell back to neutral fives, every
hook line came back empty, selection could locate none of them, and the
pipeline wrote 0 clips and 0 posts while exiting 0. Nothing in the
output distinguished it from a video with no good moments in it.
"""

from __future__ import annotations

import json
import wave

import numpy as np
import pytest

from yvc.llm.claude_cli import LLMError
from yvc.llm.guard import LLMSuccessRateError, require_success_ratio
from yvc.stages.s06_score import LLMScores, score_segments


class _Result:
    def __init__(self, data):
        self.data = data
        self.cache_hit = False


class _PartlyFailingStub:
    """Answers the first ``ok`` calls and refuses the rest."""

    def __init__(self, ok: int) -> None:
        self.ok = ok
        self.seen = 0
        self.concurrency = 1

    def complete(self, task, prompt, schema, model=None, **kwargs):
        self.seen += 1
        if self.seen > self.ok:
            raise LLMError("stubbed outage")
        return _Result(LLMScores(
            hook_3s=8, curiosity_gap=7, emotional_charge=6, standalone=7,
            audience_fit=8, hook_type="contrarian", hook_line="kanca",
            evidence_quote="bu bir test alintisidir ve yeterince uzundur",
            rationale="stub",
        ))


@pytest.fixture
def scoring_inputs(tmp_path):
    text = (
        "Bu bir test alintisidir ve yeterince uzundur diye dusunuyorum. "
        "Ucret artislari yuzde 40 seviyesinde seyrediyor bu yil boyunca. "
        "Peki bu rakam neden bu kadar yuksek sizce acaba dostlar? "
        "Sirketlerin bordro yuku her gecen ay artmaya devam ediyor."
    )
    segments = {"segments": [
        {"id": f"seg_{i:03d}", "start": float(i * 60), "end": float(i * 60 + 30),
         "text": text, "speakers": ["A", "B"]}
        for i in range(10)
    ]}
    segments_path = tmp_path / "segments.json"
    segments_path.write_text(json.dumps(segments), encoding="utf-8")

    rate = 16000
    tone = np.tile((np.sin(np.arange(rate) * 0.05) * 6000).astype("<i2"), 700)
    wav_path = tmp_path / "audio16k_raw.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(tone.tobytes())
    return segments_path, wav_path, tmp_path


# ------------------------------------------------------------------ helper

def test_a_healthy_ratio_passes():
    require_success_ratio("score", 9, 10, 0.6)


def test_a_poor_ratio_raises():
    with pytest.raises(LLMSuccessRateError, match="3/10"):
        require_success_ratio("score", 3, 10, 0.6)


def test_the_boundary_is_inclusive():
    """Exactly the configured ratio is acceptable, not a hair under."""
    require_success_ratio("score", 6, 10, 0.6)
    with pytest.raises(LLMSuccessRateError):
        require_success_ratio("score", 5, 10, 0.6)


def test_nothing_attempted_is_not_a_failure():
    """A stage with no work has nothing to be degraded about, and the
    ratio would divide by zero."""
    require_success_ratio("score", 0, 0, 0.6)


def test_a_zero_minimum_disables_the_check():
    """The escape hatch for a run that wants whatever it can get."""
    require_success_ratio("score", 0, 10, 0.0)


def test_the_message_names_the_stage_and_the_cause():
    with pytest.raises(LLMSuccessRateError) as excinfo:
        require_success_ratio("copywrite", 1, 10, 0.6)
    message = str(excinfo.value)
    assert "copywrite" in message
    assert "usage limit" in message
    assert "cached" in message  # tells the reader a re-run is cheap


# ------------------------------------------------------------- score stage

def test_scoring_fails_loudly_when_the_model_stops_answering(scoring_inputs):
    """The incident itself: every call fails, so nothing is judged."""
    segments_path, wav_path, tmp_path = scoring_inputs
    with pytest.raises(LLMSuccessRateError):
        score_segments(
            segments_path, wav_path, tmp_path / "scores.json",
            llm=_PartlyFailingStub(ok=0), model=None,
        )


def test_scoring_survives_a_minority_of_failures(scoring_inputs):
    """One bad call must not cost the run -- that is why the fallback
    exists in the first place."""
    segments_path, wav_path, tmp_path = scoring_inputs
    payload = score_segments(
        segments_path, wav_path, tmp_path / "scores.json",
        llm=_PartlyFailingStub(ok=8), model=None,
    )
    assert len(payload["segments"]) == 10
    degraded = [s for s in payload["segments"] if "llm_unavailable" in s["flags"]]
    assert len(degraded) == 2


def test_the_degraded_segments_are_flagged(scoring_inputs):
    """The artifact has to say which scores were never actually judged."""
    segments_path, wav_path, tmp_path = scoring_inputs
    payload = score_segments(
        segments_path, wav_path, tmp_path / "scores.json",
        llm=_PartlyFailingStub(ok=7), model=None,
    )
    for segment in payload["segments"]:
        if "llm_unavailable" in segment["flags"]:
            assert segment["rationale"].startswith("LLM unavailable")


def test_the_old_permissive_behaviour_is_still_reachable(scoring_inputs):
    """Setting the ratio to 0 restores exactly what the pipeline did
    before: degrade silently and keep going."""
    segments_path, wav_path, tmp_path = scoring_inputs
    payload = score_segments(
        segments_path, wav_path, tmp_path / "scores.json",
        llm=_PartlyFailingStub(ok=0), model=None, min_success_ratio=0.0,
    )
    assert len(payload["segments"]) == 10
    assert all("llm_unavailable" in s["flags"] for s in payload["segments"])
