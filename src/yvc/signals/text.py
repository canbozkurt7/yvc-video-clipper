"""Deterministic text signals for hook scoring.

These are the criteria that can be computed from the transcript alone.
They are pure functions of committed artifacts, so they produce identical
scores on every run -- which is the point. The brief rejects "the model
picked it" as an answer, and the honest response is to compute what can
be computed and reserve the LLM for genuine semantic judgement.

Everything here is Turkish-aware. Interrogatives are agglutinative
(``mı/mi/mu/mü`` attach as clitics), and the anaphora list is the actual
set of connectives that make a clip open mid-thought.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from yvc.turkish.casing import tr_lower

# Numeric and money-ish tokens. On a payroll/salary topic these are the
# highest-converting hook material, so density is scored directly.
_NUMERIC = re.compile(
    r"(?:\d+(?:[.,]\d+)*\s*%?)"
    r"|(?:%\s*\d+)"
    r"|\b(?:yüzde|bin|milyon|milyar|katı?|oran(?:ı|ında)?|lira|tl|dolar|euro|asgari)\b",
    re.IGNORECASE,
)

# Turkish interrogatives. The clitic forms are matched as separate tokens
# and as word-final suffixes, because Whisper writes them both ways.
_QUESTION_WORDS = re.compile(
    r"\b(?:ne|neden|niye|nasıl|kaç|hangi|kim|nerede|nereye|ne\s+kadar|niçin)\b",
    re.IGNORECASE,
)
_QUESTION_CLITIC = re.compile(r"\b(?:mı|mi|mu|mü)\b|\w+(?:mı|mi|mu|mü)\b", re.IGNORECASE)

# Openings that signal the clip starts mid-thought. A viewer landing here
# has no idea what "bu" refers to.
_ANAPHORA = {
    "bu", "o", "şu", "bunu", "onu", "şunu", "bunun", "onun", "şunun",
    "bunlar", "onlar", "yani", "ama", "fakat", "ancak", "dolayısıyla",
    "işte", "çünkü", "ayrıca", "yani", "böylece", "bu yüzden",
}

# Abbreviations after which a period is not a sentence boundary.
ABBREVIATIONS = {
    "dr", "sn", "vb", "vs", "bkz", "prof", "doç", "av", "mah", "cad",
    "sok", "no", "tl", "bkz", "örn", "yy", "bkz",
}


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text, flags=re.UNICODE)


def _scale(value: float, low: float, high: float) -> float:
    """Map a raw measurement onto 0-10, clipped at both ends."""
    if high <= low:
        return 0.0
    return max(0.0, min(10.0, (value - low) / (high - low) * 10.0))


@dataclass
class TextSignals:
    """Raw measurements plus their 0-10 scores, kept side by side.

    Both are reported in scores.json: the raw value is what makes a score
    auditable months later, the score is what feeds the weighted total.
    """

    word_count: int
    numeric_per_100w: float
    question_per_100w: float
    speech_rate_wps: float
    self_contained_penalties: list[dict]

    numeric_score: float
    question_score: float
    rate_score: float
    self_contained_score: float


def numeric_density(text: str) -> float:
    """Numeric/monetary tokens per 100 words."""
    words = _words(text)
    if not words:
        return 0.0
    return len(_NUMERIC.findall(text)) * 100.0 / len(words)


def question_density(text: str, first_3s_text: str = "") -> float:
    """Question markers per 100 words; an opening question counts double.

    A question in the first three seconds is the hook, not merely a
    feature of the segment, so it is weighted accordingly.
    """
    words = _words(text)
    if not words:
        return 0.0
    hits = text.count("?")
    hits += len(_QUESTION_WORDS.findall(text))
    hits += len(_QUESTION_CLITIC.findall(text))
    if first_3s_text:
        opening = (
            first_3s_text.count("?")
            + len(_QUESTION_WORDS.findall(first_3s_text))
            + len(_QUESTION_CLITIC.findall(first_3s_text))
        )
        hits += opening  # counted a second time
    return hits * 100.0 / len(words)


def speech_rate(word_count: int, duration_s: float) -> float:
    return word_count / duration_s if duration_s > 0 else 0.0


def rate_score(wps: float) -> float:
    """Triangular around 3.2 words/sec.

    Engaged Turkish conversational speech sits near 3.2 w/s. Below ~2.0 the
    delivery drags; above ~4.5 it stops being intelligible in a caption.
    Both directions are penalised rather than treating "faster is better".
    """
    peak, low, high = 3.2, 2.0, 4.5
    if wps <= low or wps >= high:
        return 0.0
    if wps <= peak:
        return _scale(wps, low, peak)
    return _scale(high - wps, 0.0, high - peak)


def self_contained(text: str, previous_segment_text: str = "") -> tuple[float, list[dict]]:
    """Penalty-based score for whether the opening stands on its own.

    Starts at 10 and subtracts. This is a deliberate counterweight to the
    LLM criteria that reward drama: without it, the rubric happily selects
    loud fragments that open with "ve bu yüzden..." and mean nothing to a
    viewer arriving cold.
    """
    penalties: list[dict] = []
    score = 10.0

    stripped = text.strip()
    words = _words(stripped)
    if not words:
        return 0.0, [{"rule": "empty_segment", "delta": -10.0}]

    first = tr_lower(words[0])
    if first in _ANAPHORA:
        score -= 4.0
        penalties.append(
            {"rule": "opens_with_connective", "token": words[0], "delta": -4.0}
        )

    # A pronoun early on with nothing in this segment to bind it to.
    opening = [tr_lower(w) for w in words[:8]]
    bare_pronouns = {"bu", "o", "şu", "bunu", "onu", "bunlar", "onlar"}
    if any(w in bare_pronouns for w in opening[1:]):
        score -= 3.0
        penalties.append({"rule": "unbound_pronoun_in_opening", "delta": -3.0})

    # Starting immediately after a question means we open mid-answer.
    if previous_segment_text.strip().endswith("?"):
        score -= 3.0
        penalties.append({"rule": "starts_mid_answer", "delta": -3.0})

    return max(0.0, score), penalties


def compute(
    text: str,
    duration_s: float,
    *,
    first_3s_text: str = "",
    previous_segment_text: str = "",
) -> TextSignals:
    """Compute every deterministic text signal for one segment."""
    words = _words(text)
    numeric = numeric_density(text)
    question = question_density(text, first_3s_text)
    wps = speech_rate(len(words), duration_s)
    sc_score, penalties = self_contained(text, previous_segment_text)

    return TextSignals(
        word_count=len(words),
        numeric_per_100w=round(numeric, 2),
        question_per_100w=round(question, 2),
        speech_rate_wps=round(wps, 2),
        self_contained_penalties=penalties,
        numeric_score=round(_scale(numeric, 0.0, 6.0), 2),
        question_score=round(_scale(question, 0.0, 5.0), 2),
        rate_score=round(rate_score(wps), 2),
        self_contained_score=round(sc_score, 2),
    )


def sentence_boundaries(
    words: list[dict],
    *,
    gap_s: float = 0.65,
    speakers: list[str] | None = None,
) -> list[dict]:
    """Build the candidate boundary list the LLM chooses from.

    The LLM is never allowed to emit a timestamp -- a hallucinated one
    lands mid-word, which the brief explicitly forbids. Instead this
    function produces boundaries deterministically and the model returns
    only their integer ids, so every boundary is a real word start by
    construction.

    A boundary exists where a word ends in terminal punctuation, or the
    silence to the next word exceeds ``gap_s``, or the speaker changes.
    """
    boundaries: list[dict] = []
    for index, word in enumerate(words[:-1]):
        token = word.get("w", "").strip()
        nxt = words[index + 1]

        reason = None
        if token.endswith((".", "?", "!", "…")) and not _is_abbreviation(token):
            reason = "punctuation"
        elif nxt.get("start", 0.0) - word.get("end", 0.0) >= gap_s:
            reason = "pause"
        elif speakers and speakers[index] != speakers[index + 1]:
            reason = "speaker_change"

        if reason:
            boundaries.append(
                {
                    "id": len(boundaries),
                    "word_index": index + 1,
                    "t": nxt.get("start", 0.0),  # always a word START
                    "reason": reason,
                }
            )
    return boundaries


def _is_abbreviation(token: str) -> bool:
    """True for 'Dr.' and for ordinals like '2023.' where '.' is not a stop."""
    body = token.rstrip(".?!…")
    if not body:
        return False
    if body.isdigit():
        return True  # Turkish ordinals: "1.", "2023."
    return tr_lower(body) in ABBREVIATIONS
