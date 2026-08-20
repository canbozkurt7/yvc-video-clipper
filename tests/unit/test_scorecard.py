"""The rubric, rendered to be read rather than parsed.

`scores.json` already holds every part of the rubric, so nothing here
computes anything -- these tests pin that the view shows what a reader
needs to answer "why did this segment win", and that it never quietly
drops a criterion or an inconvenient flag.
"""

from __future__ import annotations

import json

from yvc.report.scorecard import bar, render, show

SEGMENT = {
    "segment_id": "seg_007",
    "start": 546.3,
    "end": 629.2,
    "total": 60.7,
    "hook_type": "contrarian",
    "hook_line": "Gender pay gap büyüyor, küçülmüyor",
    "evidence_quote": "Hocam ne yazık ki gap büyüyor.",
    "rationale": "İlk cümle dikkat çekiyor ancak yeterince spesifik değil.",
    "flags": ["evidence_not_verbatim"],
    "criteria": {
        "energy": {"raw": 8.691, "unit": "dB p95-median", "score": 5.17,
                   "weight": 8, "weighted": 4.14, "method": "deterministic"},
        "numeric_density": {"raw": 1.95, "unit": "per 100 words", "score": 3.2,
                            "weight": 7, "weighted": 2.3, "method": "deterministic"},
        "hook_3s": {"raw": None, "unit": None, "score": 6.0,
                    "weight": 14, "weighted": 8.4, "method": "llm"},
    },
}


def test_the_two_halves_of_the_rubric_are_separated():
    """45 deterministic and 55 judged is the central claim about this
    rubric; a reader has to be able to see the split."""
    out = render(SEGMENT)
    assert "DETERMINISTIC" in out
    assert "JUDGED" in out
    assert "/45" in out
    assert "/55" in out


def test_every_criterion_appears():
    out = render(SEGMENT)
    for name in SEGMENT["criteria"]:
        assert name in out


def test_raw_measurements_are_shown_next_to_their_scores():
    """The deterministic half is defensible only if the number it was
    computed from is visible."""
    out = render(SEGMENT)
    assert "8.691 dB p95-median" in out
    assert "1.95 per 100 words" in out


def test_llm_criteria_show_no_raw_value():
    """A judged criterion has no measurement, and inventing a placeholder
    would blur the one distinction that matters here."""
    line = next(ln for ln in render(SEGMENT).splitlines() if "hook_3s" in ln)
    assert "None" not in line


def test_the_written_rationale_is_included():
    assert "yeterince spesifik" in render(SEGMENT)


def test_the_evidence_quote_is_included():
    assert "gap büyüyor" in render(SEGMENT)


def test_flags_are_not_hidden():
    """evidence_not_verbatim is the flag that explains why selection needs
    fuzzy matching. Suppressing it would make the clip's start look
    arbitrary."""
    out = render(SEGMENT)
    assert "evidence_not_verbatim" in out
    assert "paraphrased" in out


def test_the_threshold_verdict_is_stated():
    assert "PASS" in render(SEGMENT, threshold=55)
    assert "below threshold" in render(SEGMENT, threshold=70)


def test_a_learned_multiplier_shows_its_arithmetic():
    """When a prior has moved the score, the pre-multiplier value has to
    stay visible or the rubric total becomes unexplainable."""
    adjusted = {**SEGMENT, "base_total": 55.0, "multiplier": 1.104,
                "multiplier_basis": {"n_eff": 12.0, "y_hat": 0.45}}
    out = render(adjusted)
    assert "55.0" in out and "1.104" in out
    assert "n_eff=12.0" in out


def test_a_neutral_multiplier_adds_no_noise():
    assert "LEARNED ADJUSTMENT" not in render({**SEGMENT, "multiplier": 1.0})


def test_bar_is_proportional_and_bounded():
    assert bar(10.0).count("#") == 10
    assert bar(0.0).count("#") == 0
    assert bar(5.0).count("#") == 5
    assert len(bar(999.0)) == len(bar(-5.0)) == 10


def test_show_defaults_to_the_highest_scoring_segment(tmp_path):
    low = {**SEGMENT, "segment_id": "seg_001", "total": 35.9}
    (tmp_path / "scores.json").write_text(
        json.dumps({"segments": [low, SEGMENT]}), encoding="utf-8")
    assert "seg_007" in show(tmp_path)


def test_show_reports_an_unknown_segment_rather_than_crashing(tmp_path):
    (tmp_path / "scores.json").write_text(
        json.dumps({"segments": [SEGMENT]}), encoding="utf-8")
    out = show(tmp_path, "seg_999")
    assert "no segment" in out
    assert "seg_007" in out


def test_show_handles_an_empty_run(tmp_path):
    (tmp_path / "scores.json").write_text(
        json.dumps({"segments": []}), encoding="utf-8")
    assert "no scored segments" in show(tmp_path)
