"""The feedback loop's arithmetic and its provenance gate.

This code had no tests while it was inert. It is not inert any more: as of
`hook_v2` the multipliers computed here scale the rubric score in
`s06_score`, so a mistake now silently reshapes which clips get made. The
same thing already happened once in `s07_select` -- an untested scheduler
shipped and dropped the highest-scoring segment entirely.

The provenance tests carry the most weight. An earlier version of this
design gated on the row-level `REAL` label, which would have rejected
every real observation the pipeline can ever collect, because YouTube
returns no impression count and so its rows are always `MIXED`.
"""

from __future__ import annotations

import json

import pytest

from yvc.db.store import connect, load_priors
from yvc.feedback.priors import (
    DEFAULT_BOUNDS,
    MIN_REAL_HQS_WEIGHT,
    HookPriors,
    Outcome,
    compute_priors,
    real_hqs_weight,
    teaching_outcomes,
)
from yvc.report.analysis import HQS_WEIGHTS

TYPES = ["contrarian", "data_number", "question"]


def outcome(hook_type="contrarian", hqs=0.0, age_days=0.0, real=1.0) -> Outcome:
    return Outcome(hook_type=hook_type, hqs=hqs, age_days=age_days,
                   real_hqs_weight=real)


# --- cold start: the property the whole design rests on --------------


def test_an_unseen_hook_type_is_exactly_neutral():
    """Not approximately 1.0. A first run must score identically whether
    or not a hook database exists."""
    priors = compute_priors([], TYPES)
    assert all(p.multiplier == 1.0 for p in priors.priors.values())
    assert all(p.n_eff == 0.0 for p in priors.priors.values())


def test_an_unknown_hook_type_returns_neutral():
    assert HookPriors().multiplier("never_seen") == 1.0
    assert compute_priors([], TYPES).multiplier("not_in_list") == 1.0


# --- bounds and shrinkage --------------------------------------------


@pytest.mark.parametrize("hqs", [-50.0, -3.0, 3.0, 50.0])
def test_multipliers_stay_inside_the_bounds(hqs):
    """A genuinely excellent clip with a losing hook type must still be
    able to outrank a mediocre one, which only holds if the tilt is
    bounded."""
    low, high = DEFAULT_BOUNDS
    priors = compute_priors([outcome(hqs=hqs) for _ in range(50)], TYPES)
    for prior in priors.priors.values():
        assert low <= prior.multiplier <= high
        assert low <= prior.sampled_multiplier <= high


def test_one_observation_barely_moves_the_multiplier():
    """The small-sample guard: shrinkage with k=8 keeps a single lucky
    clip from crowning a hook type.

    Deliberately a modest hqs. At a large one the many-observation case
    saturates against the [0.80, 1.25] clip and the comparison stops
    measuring shrinkage at all -- it measures the bound.
    """
    single = compute_priors([outcome(hqs=0.5)], TYPES).multiplier(
        "contrarian", sampled=False)
    many = compute_priors([outcome(hqs=0.5) for _ in range(40)], TYPES).multiplier(
        "contrarian", sampled=False)
    assert many < DEFAULT_BOUNDS[1], "saturated: pick a smaller hqs"
    assert abs(single - 1.0) < abs(many - 1.0) / 3


def test_evidence_accumulates_toward_the_observed_direction():
    good = compute_priors([outcome(hqs=1.0) for _ in range(30)], TYPES)
    bad = compute_priors([outcome(hqs=-1.0) for _ in range(30)], TYPES)
    assert good.multiplier("contrarian", sampled=False) > 1.0
    assert bad.multiplier("contrarian", sampled=False) < 1.0


def test_old_observations_count_for_less():
    """Recency decay at tau=90d: a six-month-old result should not hold a
    verdict against fresh evidence."""
    fresh = compute_priors([outcome(hqs=1.0)], TYPES).priors["contrarian"]
    stale = compute_priors([outcome(hqs=1.0, age_days=180.0)], TYPES).priors[
        "contrarian"]
    assert stale.n_eff < fresh.n_eff / 3
    assert abs(stale.y_hat) < abs(fresh.y_hat)


# --- determinism ------------------------------------------------------


def test_the_same_seed_draws_the_same_sample():
    """Thompson sampling must not make a re-run irreproducible; two runs
    of the same video have to produce identical artifacts."""
    outcomes = [outcome(hqs=0.5) for _ in range(5)]
    first = compute_priors(outcomes, TYPES, seed="video-a")
    second = compute_priors(outcomes, TYPES, seed="video-a")
    third = compute_priors(outcomes, TYPES, seed="video-b")
    assert first.priors["contrarian"].sampled_multiplier == \
        second.priors["contrarian"].sampled_multiplier
    assert first.priors["contrarian"].sampled_multiplier != \
        third.priors["contrarian"].sampled_multiplier


def test_rarely_used_types_keep_a_wider_posterior():
    """This is what makes an under-observed hook type periodically draw
    high and get retried, rather than being locked out forever."""
    outcomes = [outcome("contrarian", hqs=0.5) for _ in range(30)]
    outcomes.append(outcome("question", hqs=0.5))
    priors = compute_priors(outcomes, TYPES)
    assert priors.priors["question"].sigma > priors.priors["contrarian"].sigma


# --- the provenance gate ---------------------------------------------


def test_a_real_youtube_row_teaches_even_though_it_is_mixed():
    """The regression that would have made the whole loop inert.

    YouTube returns no impression count, so `row_provenance` labels every
    genuinely measured row MIXED. Gating on that label discards exactly
    the data the loop exists to consume.
    """
    detail = {
        "hook_retention_3s": "REAL",   # 0.45
        "completion_rate": "REAL",     # 0.25
        "engagement_rate": "REAL",     # 0.20
        "ctr": "SIMULATED",            # 0.10
        "impressions": "SIMULATED",
        "reach": "SIMULATED",
    }
    weight = real_hqs_weight(detail, HQS_WEIGHTS)
    assert weight == pytest.approx(0.90)
    assert outcome(real=weight).teaches


def test_retention_and_completion_alone_are_enough():
    """What a YouTube row looked like before engagement_rate was derived
    from real counts: 0.70, still above the bar."""
    detail = {"hook_retention_3s": "REAL", "completion_rate": "REAL"}
    assert real_hqs_weight(detail, HQS_WEIGHTS) == pytest.approx(0.70)
    assert outcome(real=0.70).teaches


def test_without_a_real_retention_curve_nothing_is_learned():
    """hook_retention_3s carries 0.45 of HQS, so everything else combined
    cannot clear the bar. That is deliberate: retention is the only
    direct measure of whether the hook did its job."""
    detail = {"completion_rate": "REAL", "engagement_rate": "REAL",
              "ctr": "REAL"}
    weight = real_hqs_weight(detail, HQS_WEIGHTS)
    assert weight == pytest.approx(0.55)
    assert weight < MIN_REAL_HQS_WEIGHT
    assert not outcome(real=weight).teaches


def test_fully_simulated_rows_teach_nothing():
    detail = dict.fromkeys(HQS_WEIGHTS, "SIMULATED")
    assert real_hqs_weight(detail, HQS_WEIGHTS) == 0.0
    assert real_hqs_weight({}, HQS_WEIGHTS) == 0.0
    assert real_hqs_weight(None, HQS_WEIGHTS) == 0.0


def test_a_simulated_history_leaves_every_multiplier_neutral():
    """Learning from the simulator would mean learning back its own
    hook-type assumptions -- circular, and indistinguishable from a
    working loop unless it is blocked here."""
    simulated = [outcome(hqs=2.0, real=0.0) for _ in range(40)]
    assert teaching_outcomes(simulated) == []
    priors = compute_priors(teaching_outcomes(simulated), TYPES)
    assert all(p.multiplier == 1.0 for p in priors.priors.values())


def test_the_gate_keeps_measured_rows_and_drops_the_rest():
    mixed = [outcome(hqs=1.0, real=0.9), outcome(hqs=1.0, real=0.0),
             outcome(hqs=1.0, real=0.55)]
    kept = teaching_outcomes(mixed)
    assert [o.real_hqs_weight for o in kept] == [0.9]


# --- reading priors back ---------------------------------------------


def test_a_missing_database_yields_neutral_priors(tmp_path):
    priors = load_priors(tmp_path / "absent.db")
    assert priors.priors == {}
    assert priors.multiplier("contrarian") == 1.0


def test_an_empty_table_yields_neutral_priors(tmp_path):
    path = tmp_path / "empty.db"
    with connect(path):
        pass
    assert load_priors(path).priors == {}


def test_the_newest_snapshot_per_hook_type_wins(tmp_path):
    """Priors accumulate across videos; scoring must read the current
    verdict, not the first one ever written."""
    path = tmp_path / "hooks.db"
    rows = [
        ("old|contrarian", "2026-01-01 00:00:00", 1.05),
        ("new|contrarian", "2026-08-01 00:00:00", 1.20),
        ("only|question", "2026-03-01 00:00:00", 0.90),
    ]
    with connect(path) as conn:
        for snapshot_id, created, multiplier in rows:
            conn.execute(
                "INSERT INTO hook_priors_snapshot (snapshot_id, run_id, "
                "created_at, hook_type, n_eff, y_bar, y_hat, sigma, "
                "multiplier, sampled_multiplier, params) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot_id, "r", created, snapshot_id.split("|")[1],
                 5.0, 0.1, 0.1, 0.2, multiplier, multiplier,
                 json.dumps({"eta": 0.35})),
            )

    priors = load_priors(path)
    assert priors.multiplier("contrarian", sampled=False) == 1.20
    assert priors.multiplier("question", sampled=False) == 0.90
    assert priors.multiplier("story") == 1.0
    assert priors.params.get("eta") == 0.35
