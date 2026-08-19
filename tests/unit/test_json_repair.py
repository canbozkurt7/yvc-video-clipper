"""JSON repair ladder.

The copywriting stage asks the model for a *verbatim quotation* from a
Turkish transcript, which invites it to write the one character that
breaks JSON. This was not theoretical: two consecutive ~600 s calls were
lost to a single unescaped quote, which is the entire stage's budget.

The tests pin both directions -- broken input recovers, and valid input
is never "repaired" into something different.
"""

from __future__ import annotations

import json

import pytest

from yvc.llm.claude_cli import ClaudeCLI, LLMSchemaError, _escape_inner_quotes

extract = ClaudeCLI._extract_json


# --- the regression --------------------------------------------------


def test_unescaped_inner_quotes_are_recovered():
    broken = '{"evidence_quote": "ne yazik ki "gap" buyuyor.", "angle": "celiski"}'
    assert extract(broken)["evidence_quote"] == 'ne yazik ki "gap" buyuyor.'


def test_multiple_inner_quotes_in_several_fields():
    broken = (
        '{"a": "he said "yes" loudly", "b": "she said "no" quietly"}'
    )
    parsed = extract(broken)
    assert parsed["a"] == 'he said "yes" loudly'
    assert parsed["b"] == 'she said "no" quietly'


def test_quote_before_a_structural_character_still_terminates():
    """The scanner must not swallow legitimate terminators."""
    assert extract('{"a": "x", "b": "y"}') == {"a": "x", "b": "y"}
    assert extract('{"a": ["p", "q"]}') == {"a": ["p", "q"]}


def test_turkish_text_survives_repair():
    broken = '{"q": "Hocam «gap» degil, "ucret farki" diyoruz.", "t": "ok"}'
    parsed = extract(broken)
    assert parsed["q"] == 'Hocam «gap» degil, "ucret farki" diyoruz.'
    assert "ç" not in parsed["t"]


# --- the ladder's earlier rungs still work ---------------------------


def test_code_fences_are_stripped():
    assert extract('```json\n{"a": 1}\n```') == {"a": 1}


def test_prose_preamble_is_skipped():
    assert extract('Iste sonuc:\n{"a": 1}\nUmarim yardimci olur.') == {"a": 1}


def test_trailing_commas_are_tolerated():
    assert extract('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}


def test_smart_quotes_are_normalised():
    assert extract('{\u201ca\u201d: 1}') == {"a": 1}


def test_genuinely_unparseable_input_raises():
    with pytest.raises(LLMSchemaError, match="unparseable JSON"):
        extract("this is not json at all, not even close")


# --- the repair must be a no-op on healthy input ---------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"a": "plain"},
        {"a": 'already \\"escaped\\" here'},
        {"nested": {"list": [1, 2, {"deep": "value"}]}},
        {"unicode": "cgiosu ÇĞİÖŞÜ çğıöşü"},
        {"empty": "", "null": None, "bool": True},
        {"punctuation": "a, b: c} d] e"},
    ],
)
def test_valid_json_is_unchanged(payload):
    text = json.dumps(payload, ensure_ascii=False)
    assert _escape_inner_quotes(text) == text
    assert extract(text) == payload


def test_escaped_quotes_are_not_double_escaped():
    text = '{"a": "he said \\"hi\\""}'
    assert json.loads(_escape_inner_quotes(text))["a"] == 'he said "hi"'


def test_backslash_before_quote_is_respected():
    """A trailing backslash inside a string must not break the scanner."""
    text = '{"path": "C:\\\\dir\\\\", "next": 1}'
    assert json.loads(_escape_inner_quotes(text))["path"] == "C:\\dir\\"
