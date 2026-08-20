"""Caption line wrapping.

This module shipped untested and the cost was real: `wrap_lines` silently
dropped every word that did not fit within `max_lines`, so 35% of the
caption groups in the five delivered clips were missing speech -- 106
words in total -- and the burned-in hook overlay rendered as the
fragment "150 BİN KİŞİNİN MAAŞINI", verb removed.

The invariant these tests exist to hold is simple and absolute: wrapping
may rebalance lines, but it may never lose a word. An overlong line is a
cosmetic problem; missing speech is a wrong caption.
"""

from __future__ import annotations

import pytest

from yvc.render.subtitles import wrap_lines


def kept(words: list[str], lines: list[list[int]]) -> list[str]:
    return [words[i] for line in lines for i in line]


# --- the invariant ----------------------------------------------------


@pytest.mark.parametrize("text", [
    "150 BİN KİŞİNİN MAAŞINI HESAPLIYORUZ",
    "TÜRKİYE BAZI SEKTÖRLERDE AVRUPAYI GEÇİYOR",
    "GENDER PAY GAP BÜYÜYOR KÜÇÜLMÜYOR",
    "MAAŞLARIN VERGİLERİNİ VESAİRELERİNİ HESAPLIYORUZ ŞİRKETLER İÇİN",
    "BELİRLENMESİNDE KARŞILAŞTIRILMASINDA DEĞERLENDİRİLMESİNDE",
    "A B C D E F G H I J K L M N O P",
])
def test_no_word_is_ever_dropped(text):
    words = text.split()
    assert kept(words, wrap_lines(words, 16)) == words


def test_no_word_is_dropped_at_any_line_width():
    words = "150 BİN KİŞİNİN MAAŞINI HESAPLIYORUZ ŞİRKETLER İÇİN".split()
    for width in range(4, 40):
        assert kept(words, wrap_lines(words, width)) == words, f"width={width}"


def test_no_word_is_dropped_at_any_max_lines():
    words = "TÜRKİYE BAZI SEKTÖRLERDE AVRUPAYI GEÇİYOR ARTIK".split()
    for limit in (1, 2, 3, 4):
        assert kept(words, wrap_lines(words, 16, max_lines=limit)) == words


def test_order_is_preserved():
    words = "BİR İKİ ÜÇ DÖRT BEŞ ALTI YEDİ".split()
    lines = wrap_lines(words, 12)
    flat = [i for line in lines for i in line]
    assert flat == sorted(flat)


# --- the regression, named --------------------------------------------


def test_the_overflowing_tail_joins_the_last_line():
    """The specific defect: the tail became a third line and was then
    sliced off by the max_lines cap, so it vanished entirely."""
    words = "150 BİN KİŞİNİN MAAŞINI HESAPLIYORUZ".split()
    lines = wrap_lines(words, 16, max_lines=2)
    assert len(lines) == 2
    assert [words[i] for i in lines[-1]] == ["MAAŞINI", "HESAPLIYORUZ"]


def test_a_word_longer_than_the_line_still_survives():
    words = ["KARŞILAŞTIRILMASINDA", "VE", "SONRASINDA"]
    assert kept(words, wrap_lines(words, 8)) == words


# --- shape ------------------------------------------------------------


def test_respects_max_lines():
    words = "A B C D E F G H I J K L".split()
    assert len(wrap_lines(words, 4, max_lines=2)) <= 2


def test_short_text_stays_on_one_line():
    words = ["KISA", "METİN"]
    assert wrap_lines(words, 20) == [[0, 1]]


def test_empty_input():
    assert wrap_lines([], 16) == []
