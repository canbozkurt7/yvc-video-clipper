"""A segment that produces no clip has to say why.

Windows open on the hook and end inside the same segment, so a hook near
the end of its segment leaves no room for one. On the reference video
that silently removed seg_006 -- the highest-scoring segment in the
video, 70.1 -- while windows scoring 33.8 and 31.5 were rendered. The
selection rule is deliberate; losing the best segment without a word is
not.
"""

from __future__ import annotations

from yvc.io import read_json, write_json
from yvc.stages.s07_select import select


def _words(text: str, start: float, step: float = 0.5) -> list[dict]:
    return [
        {"w": word, "start": round(start + i * step, 3),
         "end": round(start + (i + 1) * step, 3)}
        for i, word in enumerate(text.split())
    ]


LEAD = "Bu bolumde ucret politikalarini konusuyoruz uzun uzun ve sakin bir sekilde. " * 3
HOOK = "Aradaki fark kapanmiyor tam tersine buyuyor."


def _fixture(tmp_path):
    # 40 seconds of lead-in, then the hook in the last 2 seconds: located,
    # but with nothing after it to build a 20-second window from.
    lead_words = _words(LEAD, 0.0)
    hook_words = _words(HOOK, lead_words[-1]["end"] + 0.1, step=0.4)
    end = hook_words[-1]["end"]

    write_json(tmp_path / "transcript.json", {"segments": [
        {"words": lead_words + hook_words},
    ]})
    write_json(tmp_path / "segments.json", {"segments": [
        {"id": "seg_000", "start": 0.0, "end": end, "text": LEAD + HOOK},
    ]})
    write_json(tmp_path / "scores.json", {"segments": [
        {"segment_id": "seg_000", "start": 0.0, "end": end, "total": 70.1,
         "hook_type": "contrarian", "hook_line": "Fark buyuyor",
         "evidence_quote": HOOK, "criteria": {}, "flags": []},
    ]})
    return tmp_path


def test_a_segment_whose_hook_lands_too_late_is_named_not_dropped_in_silence(tmp_path):
    base = _fixture(tmp_path)

    select(base / "scores.json", base / "clips.json",
           segments_path=base / "segments.json",
           transcript_path=base / "transcript.json")

    dropped = read_json(base / "clips.json")["dropped"]["hook_too_late_for_window"]
    assert [d["segment_id"] for d in dropped] == ["seg_000"]
    assert dropped[0]["total"] == 70.1
    assert dropped[0]["room_after_hook_s"] < dropped[0]["needs_s"], (
        "the record has to carry the measurement that explains the drop, "
        "not just the fact of it"
    )


def test_the_record_distinguishes_that_from_a_hook_it_could_not_find(tmp_path):
    base = _fixture(tmp_path)

    select(base / "scores.json", base / "clips.json",
           segments_path=base / "segments.json",
           transcript_path=base / "transcript.json")

    dropped = read_json(base / "clips.json")["dropped"]
    assert dropped["hook_not_locatable"] == [], (
        "the hook was found; reporting it as unfindable would send the "
        "reader to the scoring stage instead of the window length"
    )
