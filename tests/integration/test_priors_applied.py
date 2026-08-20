"""The learned multiplier, applied end to end through the scoring stage.

Two properties matter here and neither is visible from the priors module
alone:

* **Inertness.** Until something has actually been measured, every
  multiplier is 1.0 and scoring must produce exactly what it produced
  before the feedback loop existed. This is the safety net for shipping
  a change that touches every score in the system.
* **Auditability.** When a multiplier does apply, the record has to carry
  the pre-multiplier score and the evidence behind the adjustment.
  Otherwise "why did this clip win" becomes unanswerable months later,
  which is the exact failure the written rubric exists to prevent.

A stub LLM keeps this a test of the arithmetic rather than of Claude:
the real stage costs ~120 s per segment.
"""

from __future__ import annotations

import json
import wave

import numpy as np
import pytest

from yvc.feedback.priors import HookPrior, HookPriors
from yvc.stages.s06_score import LLMScores, score_segments


class StubResult:
    def __init__(self, data):
        self.data = data


class StubLLM:
    """Returns a fixed judgement, so any score difference comes from the
    multiplier and nothing else."""

    def __init__(self, hook_type="contrarian"):
        self.hook_type = hook_type

    def complete(self, task, prompt, schema, model=None, **kwargs):
        return StubResult(LLMScores(
            hook_3s=8, curiosity_gap=7, emotional_charge=6, standalone=7,
            audience_fit=8, hook_type=self.hook_type,
            hook_line="test kancasi",
            evidence_quote="bu bir test alintisidir ve yeterince uzundur",
            rationale="stub",
        ))


@pytest.fixture
def fixture_run(tmp_path):
    """A minimal segments.json plus the wav the deterministic signals read."""
    text = (
        "Bu bir test alintisidir ve yeterince uzundur diye dusunuyorum. "
        "Ucret artislari yuzde 40 seviyesinde seyrediyor bu yil. "
        "Peki bu rakam neden bu kadar yuksek sizce acaba? "
        "Sirketlerin bordro yuku her gecen ay artmaya devam ediyor."
    )
    segments = {"segments": [
        {"id": "seg_001", "start": 0.0, "end": 30.0, "text": text,
         "speakers": ["A", "B"]},
        {"id": "seg_002", "start": 40.0, "end": 70.0, "text": text,
         "speakers": ["A"]},
    ]}
    segments_path = tmp_path / "segments.json"
    segments_path.write_text(json.dumps(segments), encoding="utf-8")

    wav_path = tmp_path / "audio16k_raw.wav"
    rate = 16000
    tone = (np.sin(np.arange(rate * 80) * 0.05) * 6000).astype("<i2")
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(tone.tobytes())

    return segments_path, wav_path, tmp_path


def run(fixture, priors, name="scores.json"):
    segments_path, wav_path, tmp_path = fixture
    return score_segments(
        segments_path, wav_path, tmp_path / name,
        llm=StubLLM(), model=None, priors=priors,
    )


def test_no_priors_and_neutral_priors_score_identically(fixture_run):
    """The regression net for the whole change: with nothing measured,
    the pipeline must behave exactly as it did before."""
    without = run(fixture_run, None, "a.json")
    neutral = run(fixture_run, HookPriors(), "b.json")

    assert [s["total"] for s in without["segments"]] == \
           [s["total"] for s in neutral["segments"]]
    assert all(s["multiplier"] == 1.0 for s in without["segments"])
    assert all(s["total"] == s["base_total"] for s in without["segments"])


def test_an_unmeasured_hook_type_is_left_alone(fixture_run):
    """Priors exist, but not for this hook type. Cold start is neutral,
    not random and not penalised."""
    priors = HookPriors(priors={"story": HookPrior(
        hook_type="story", n_eff=9.0, y_bar=0.5, y_hat=0.4, sigma=0.2,
        multiplier=1.2, sampled_multiplier=1.2)})
    scored = run(fixture_run, priors)
    assert all(s["multiplier"] == 1.0 for s in scored["segments"])


def test_a_learned_multiplier_scales_the_rubric_score(fixture_run):
    priors = HookPriors(priors={"contrarian": HookPrior(
        hook_type="contrarian", n_eff=12.0, y_bar=0.6, y_hat=0.45,
        sigma=0.17, multiplier=1.17, sampled_multiplier=1.2)})
    scored = run(fixture_run, priors)

    for segment in scored["segments"]:
        assert segment["multiplier"] == 1.2
        assert segment["total"] == pytest.approx(
            segment["base_total"] * 1.2, abs=0.02)


def test_the_adjustment_records_its_evidence(fixture_run):
    """A bare multiplier is not auditable; the basis has to travel with
    it so a past decision can be reconstructed."""
    priors = HookPriors(priors={"contrarian": HookPrior(
        hook_type="contrarian", n_eff=12.0, y_bar=0.6, y_hat=0.45,
        sigma=0.17, multiplier=1.17, sampled_multiplier=1.2)})
    basis = run(fixture_run, priors)["segments"][0]["multiplier_basis"]
    assert basis["n_eff"] == 12.0
    assert basis["y_hat"] == 0.45
    assert basis["mean_multiplier"] == 1.17


def test_a_penalising_multiplier_lowers_the_score(fixture_run):
    priors = HookPriors(priors={"contrarian": HookPrior(
        hook_type="contrarian", n_eff=20.0, y_bar=-0.8, y_hat=-0.6,
        sigma=0.13, multiplier=0.81, sampled_multiplier=0.81)})
    scored = run(fixture_run, priors)
    for segment in scored["segments"]:
        assert segment["total"] < segment["base_total"]


def test_the_rubric_version_marks_the_change(fixture_run):
    """Stage fingerprints key off this; a stale scores.json from the
    pre-multiplier rubric must not be treated as current."""
    assert run(fixture_run, None)["rubric_version"] == "hook_v2"
