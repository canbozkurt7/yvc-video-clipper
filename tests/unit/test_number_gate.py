"""The number gate has to reject fabrication without rejecting fact.

It squashed the whole `key_number` field into a single digit string, so
"18 milyon / 30 milyon" -- both numbers said aloud in the clip -- became
a search for "1830" and the copy was flagged as hallucinated. Three of
thirteen posts on the reference video carried that false positive.
"""

from __future__ import annotations

import pytest

from yvc.stages.s09_copywrite import ClipCopy, PlatformCopy, validate_copy

CLIP = (
    "Bugun 30 milyon ucretlinin 18 milyonu asgari ucrete yakin calisiyor. "
    "Asgari ucretin toplam icindeki payi yuzde 60 seviyesinde duruyor. "
    "Bu oran 2011 ile 2014 arasinda bu kadar yuksek degildi hic."
)
QUOTE = "Bugun 30 milyon ucretlinin 18 milyonu asgari ucrete yakin calisiyor"


def _copy(key_number: str) -> ClipCopy:
    block = PlatformCopy(body="Kisa metin.", hashtags=["#a", "#b", "#c"], cta="oku")
    x_block = PlatformCopy(body="Kisa metin.", hashtags=["#a"], cta="oku")
    return ClipCopy(
        linkedin=block, instagram=PlatformCopy(
            body="Kisa metin.", hashtags=["#a"] * 9, cta="oku"),
        x=x_block, tiktok=block, youtube=block,
        evidence_quote=QUOTE, angle="rakam", key_number=key_number,
    )


def _codes(key_number: str) -> list[str]:
    return [i["code"] for i in validate_copy(_copy(key_number), CLIP, [])]


@pytest.mark.parametrize("key_number", ["18 milyon / 30 milyon", "%60", "2011-2014"])
def test_numbers_the_clip_actually_says_pass(key_number):
    assert "NUMBER_HALLUCINATION" not in _codes(key_number)


def test_a_number_nobody_said_is_still_caught():
    assert "NUMBER_HALLUCINATION" in _codes("%97")


def test_one_invented_number_beside_a_real_one_is_caught():
    assert "NUMBER_HALLUCINATION" in _codes("30 milyon / %97")


def test_the_message_names_which_number_was_not_found():
    issues = validate_copy(_copy("30 milyon / %97"), CLIP, [])
    detail = next(i["detail"] for i in issues if i["code"] == "NUMBER_HALLUCINATION")

    assert "97" in detail
    assert "30" not in detail.split(":", 1)[1], (
        "naming the grounded number too would send the reader looking for "
        "a problem that is not there"
    )
