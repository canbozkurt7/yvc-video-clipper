"""Clip selection: scheduling optimality and boundary timing.

The scheduler had a real bug that reached real data: it maximised total
score over any number of intervals and truncated to N afterwards, so two
mediocre windows outranked one excellent one and the quota filled with
filler. These tests pin the corrected semantics -- best N, not best sum.
"""

from __future__ import annotations

from yvc.stages.s07_select import (
    Window,
    candidate_windows,
    flatten_words,
    schedule_non_overlapping,
    sentences_from_words,
)


def w(start: float, end: float, score: float, seg: str = "seg_x") -> Window:
    return Window(
        segment_id=seg, start=start, end=end, text="t", score=score,
        hook_type="contrarian", hook_line="h", evidence_quote="",
        contains_evidence=False, overlap_fraction=1.0,
    )


def test_one_strong_window_beats_two_mediocre_ones():
    """The regression. Observed on the real video: a 62.9 window was
    dropped for a 43.0 + 38.3 pair from the same segment."""
    windows = [w(125, 146, 43.0), w(152, 173, 38.3), w(125, 173, 62.9)]
    picked = schedule_non_overlapping(windows, 1, min_gap_s=5.0)
    assert [p.score for p in picked] == [62.9]


def test_returns_at_most_count():
    windows = [w(0, 10, 50), w(20, 30, 40), w(40, 50, 30), w(60, 70, 20)]
    assert len(schedule_non_overlapping(windows, 2, min_gap_s=5.0)) == 2


def test_picks_the_best_n_not_the_densest_packing():
    """Four cheap windows sum higher than two great ones, but with N=2
    the two great ones must win."""
    windows = [
        w(0, 10, 10), w(20, 30, 10), w(40, 50, 10), w(60, 70, 10),
        w(0, 30, 45), w(40, 70, 44),
    ]
    picked = schedule_non_overlapping(windows, 2, min_gap_s=5.0)
    assert sorted(p.score for p in picked) == [44, 45]


def test_selection_is_optimal_not_greedy():
    """Greedy-by-score takes the 50 and blocks both 30s; optimal takes
    the pair."""
    windows = [w(0, 100, 50), w(0, 40, 30), w(50, 90, 30)]
    picked = schedule_non_overlapping(windows, 2, min_gap_s=5.0)
    assert sum(p.score for p in picked) == 60


def test_min_gap_is_enforced():
    picked = schedule_non_overlapping([w(0, 10, 50), w(12, 20, 50)], 2, min_gap_s=5.0)
    assert len(picked) == 1


def test_fewer_windows_than_requested_is_not_a_crash():
    assert len(schedule_non_overlapping([w(0, 10, 50)], 3, min_gap_s=5.0)) == 1
    assert schedule_non_overlapping([], 3, min_gap_s=5.0) == []
    assert schedule_non_overlapping([w(0, 10, 5)], 0, min_gap_s=5.0) == []


# --- boundary timing ------------------------------------------------


TRANSCRIPT = {
    "segments": [
        {
            "words": [
                {"w": "Ucret", "start": 0.0, "end": 0.5},
                {"w": "artiyor.", "start": 0.5, "end": 1.2},
                {"w": "Gap", "start": 2.0, "end": 2.4},
                {"w": "buyuyor.", "start": 2.4, "end": 3.6},
            ]
        }
    ]
}


def test_sentences_use_real_word_timings():
    words = flatten_words(TRANSCRIPT)
    units = sentences_from_words(words, 0.0, 4.0)
    assert len(units) == 2
    # Exact word boundaries, not interpolated from character counts.
    assert units[0][1] == 0.0 and units[0][2] == 1.2
    assert units[1][1] == 2.0 and units[1][2] == 3.6


def test_sentences_outside_the_span_are_excluded():
    words = flatten_words(TRANSCRIPT)
    assert sentences_from_words(words, 10.0, 20.0) == []


def test_candidate_windows_falls_back_to_text_when_no_timings():
    scored = {
        "segment_id": "seg_1", "start": 0.0, "end": 60.0, "total": 50.0,
        "text": "Bir. Iki. Uc. Dort.", "evidence_quote": "",
    }
    assert candidate_windows(scored, 20, 60) != []


def test_evidence_bearing_window_outranks_an_equal_one_without_it():
    scored = {
        "segment_id": "seg_1", "start": 0.0, "end": 40.0, "total": 50.0,
        "text": "", "evidence_quote": "gap buyuyor ve bu ciddi bir sorun",
    }
    units = [
        ("gap buyuyor ve bu ciddi bir sorun.", 0.0, 20.0),
        ("baska bir cumle burada duruyor.", 20.0, 40.0),
    ]
    windows = candidate_windows(scored, 19, 21, sentences=units)
    by_evidence = {win.contains_evidence: win.score for win in windows}
    assert by_evidence[True] > by_evidence[False]


# --- hook anchoring --------------------------------------------------
#
# The defect these pin: clips were built by duration fit with "contains
# the hook" worth a +5 bonus, so 4 of 5 rendered clips opened on filler
# while the burned-in overlay promised a claim the audio never made.

from yvc.stages.s07_select import (  # noqa: E402
    hook_anchored_windows,
    locate_hook,
    overlay_matches_opening,
)

SENTENCES = [
    ("Kulturel bir sey degil ya o anlamda.", 0.0, 4.0),
    ("En azindan payroll uzerinde.", 4.0, 7.0),
    ("Gender pay gap kadin aleyhine calisiyor tabii ki.", 7.0, 12.0),
    ("Azaliyor mu peki?", 12.0, 14.0),
    ("Hocam ne yazik ki gap buyuyor.", 14.0, 18.0),
    ("Bize negatif geliyor bu tablo.", 18.0, 24.0),
]


def test_locate_hook_finds_the_claim_not_the_run_up():
    index, confidence = locate_hook(
        SENTENCES, "Gender pay gap kadin aleyhine calisiyor tabii ki."
    )
    assert index == 2, "anchored on the preamble instead of the claim"
    assert confidence > 0.9


def test_locate_hook_tolerates_paraphrase():
    """The model paraphrases its own quote in 54% of segments; exact
    matching silently discarded the highest-scoring ones."""
    index, confidence = locate_hook(SENTENCES, "gender pay gap kadin aleyhine")
    assert index == 2
    assert confidence >= 0.34


def test_locate_hook_ignores_diacritics_and_case():
    index, _ = locate_hook(SENTENCES, "GENDER PAY GAP KADIN ALEYHİNE")
    assert index == 2


def test_locate_hook_falls_back_to_hook_line():
    index, _ = locate_hook(SENTENCES, "", hook_line="hocam gap buyuyor yazik")
    assert index == 4


def test_locate_hook_reports_failure_rather_than_guessing():
    index, confidence = locate_hook(SENTENCES, "tamamen alakasiz bir konu hakkinda")
    assert index == -1 and confidence == 0.0


def test_locate_hook_rejects_too_short_a_quote():
    """A two-word quote matches almost anything."""
    assert locate_hook(SENTENCES, "bir sey")[0] == -1


def test_locate_hook_on_empty_input():
    assert locate_hook([], "anything at all here")[0] == -1


SCORED = {
    "segment_id": "seg_007", "start": 0.0, "end": 24.0, "total": 60.0,
    "hook_type": "contrarian", "hook_line": "Gender pay gap buyuyor",
    "evidence_quote": "Gender pay gap kadin aleyhine calisiyor tabii ki.",
}


def test_every_anchored_window_opens_on_the_hook():
    windows = hook_anchored_windows(SCORED, 5, 20, sentences=SENTENCES)
    assert windows
    for window in windows:
        # Sentence 2 starts at 7.0; a lead-in may begin at sentence 1 (4.0).
        assert window.start in (4.0, 7.0), f"opened at {window.start}"
        assert window.contains_evidence


def test_opening_exactly_on_the_hook_outranks_a_run_up():
    windows = hook_anchored_windows(SCORED, 5, 20, sentences=SENTENCES)
    exact = max(w.score for w in windows if w.start == 7.0)
    run_up = max((w.score for w in windows if w.start == 4.0), default=-1)
    assert exact > run_up


def test_unlocatable_hook_yields_no_candidates():
    """No clip beats a clip about nothing."""
    scored = {**SCORED, "evidence_quote": "bambaska bir konu burada", "hook_line": ""}
    assert hook_anchored_windows(scored, 5, 20, sentences=SENTENCES) == []


# --- overlay honesty -------------------------------------------------


def test_overlay_kept_when_it_matches_the_opening():
    assert overlay_matches_opening(
        "Gender pay gap buyuyor",
        "Gender pay gap kadin aleyhine calisiyor tabii ki. Gap buyuyor.",
    )


def test_overlay_rejected_when_it_promises_another_claim():
    assert not overlay_matches_opening(
        "Zam mi istiyorsun? Is degistir.",
        "Bu sefer beklentileri cok yuksek ve ucurumdan asagi dusmek uzere",
    )


def test_overlay_of_only_function_words_is_rejected():
    assert not overlay_matches_opening("bu ve bir", "bu ve bir seyler")


# --- exploration quota ------------------------------------------------
# One of three designed guards against runaway convergence, and the only
# one that was never written. Bounds and Thompson sampling damp the
# effect; neither forces the comparison to actually happen. A hook type
# that stops being posted stops accumulating evidence, so its multiplier
# decays toward neutral instead of being disproved.

from yvc.stages.s07_select import (  # noqa: E402
    conflicts,
    exploration_quota,
    top_hook_types,
    with_exploration,
)


class FakePrior:
    def __init__(self, hook_type, multiplier):
        self.hook_type = hook_type
        self.multiplier = multiplier


class FakePriors:
    def __init__(self, **types):
        self.priors = {k: FakePrior(k, v) for k, v in types.items()}


def typed(start, end, score, hook_type, seg="seg_x") -> Window:
    window = w(start, end, score, seg)
    window.hook_type = hook_type
    return window


def test_quota_is_at_least_one_slot():
    assert exploration_quota(3) == 1
    assert exploration_quota(2) == 1
    assert exploration_quota(10) == 2
    assert exploration_quota(0) == 0


def test_top_types_are_the_ones_being_exploited():
    priors = FakePriors(contrarian=1.20, data_number=1.10, question=0.90)
    assert top_hook_types(priors) == {"contrarian", "data_number"}


def test_no_priors_means_nothing_is_exploited():
    assert top_hook_types(None) == set()
    assert top_hook_types(FakePriors()) == set()


def test_a_reserved_slot_goes_to_a_non_exploited_type():
    pool = [
        typed(0, 20, 90, "contrarian"), typed(30, 50, 88, "contrarian"),
        typed(60, 80, 86, "contrarian"), typed(90, 110, 40, "story"),
    ]
    picked = schedule_non_overlapping(pool, 3, min_gap_s=5.0)
    assert {p.hook_type for p in picked} == {"contrarian"}

    selection, explored = with_exploration(
        pool, picked, count=3, min_gap_s=5.0,
        exploited={"contrarian", "data_number"},
    )
    assert len(explored) == 1
    assert explored[0].hook_type == "story"
    assert len(selection) == 3
    assert any(s.hook_type == "story" for s in selection)


def test_the_quota_never_breaks_non_overlap():
    """The reason this rebuilds through the scheduler instead of swapping
    picked windows: a hand-swap silently violates min_gap."""
    pool = [
        typed(0, 20, 90, "contrarian"), typed(22, 40, 88, "contrarian"),
        typed(18, 35, 50, "story"), typed(60, 80, 45, "story"),
    ]
    picked = schedule_non_overlapping(pool, 2, min_gap_s=5.0)
    selection, _ = with_exploration(
        pool, picked, count=2, min_gap_s=5.0, exploited={"contrarian"},
    )
    for i, a in enumerate(selection):
        for b in selection[i + 1:]:
            assert a.end + 5.0 <= b.start or b.end + 5.0 <= a.start


def test_quota_is_a_no_op_without_priors():
    """Until something has been measured, selection must be exactly what
    it is today."""
    pool = [typed(0, 20, 90, "contrarian"), typed(30, 50, 40, "story")]
    picked = schedule_non_overlapping(pool, 1, min_gap_s=5.0)
    selection, explored = with_exploration(
        pool, picked, count=1, min_gap_s=5.0, exploited=set(),
    )
    assert selection == picked
    assert explored == []


def test_an_already_diverse_selection_is_left_alone():
    pool = [typed(0, 20, 90, "contrarian"), typed(30, 50, 80, "story")]
    picked = schedule_non_overlapping(pool, 2, min_gap_s=5.0)
    selection, explored = with_exploration(
        pool, picked, count=2, min_gap_s=5.0, exploited={"contrarian"},
    )
    assert explored == []
    assert selection == picked


def test_no_alternative_type_available_keeps_the_greedy_pick():
    """A quota that cannot be met must not empty the selection. Filling
    the format's quota still outranks exploring."""
    pool = [typed(0, 20, 90, "contrarian"), typed(30, 50, 80, "contrarian")]
    picked = schedule_non_overlapping(pool, 2, min_gap_s=5.0)
    selection, explored = with_exploration(
        pool, picked, count=2, min_gap_s=5.0, exploited={"contrarian"},
    )
    assert selection == picked
    assert explored == []


def test_conflicts_respects_the_gap():
    chosen = [typed(0, 20, 90, "contrarian")]
    assert conflicts(typed(22, 40, 50, "story"), chosen, 5.0)
    assert not conflicts(typed(26, 40, 50, "story"), chosen, 5.0)


def test_neutral_priors_exploit_nothing():
    """The regression: with every multiplier at 1.0, ranking still yields
    a "top two", and the quota then displaced a 60.9-scoring clip set for
    one containing a 31.1 -- exploring alternatives to a preference that
    had never been learned."""
    assert top_hook_types(FakePriors(contrarian=1.0, data_number=1.0,
                                     question=1.0)) == set()


def test_only_favoured_types_count_as_exploited():
    priors = FakePriors(contrarian=1.15, data_number=1.0, question=0.85)
    assert top_hook_types(priors) == {"contrarian"}
