"""Turkish-correct case conversion and Unicode normalization.

Python's ``str.upper()`` and ``str.lower()`` implement invariant casing,
which is wrong for Turkish in two specific places:

    "istanbul".upper()  -> "ISTANBUL"   (should be "İSTANBUL")
    "IK".lower()        -> "ik"         (should be "ık")

The second one has a nastier variant. ``"İ".lower()`` does not return
``"i"`` -- it returns ``"i"`` followed by U+0307 COMBINING DOT ABOVE, a
two-codepoint string. That silently breaks string equality and dictionary
lookups, and in libass it renders as a stray floating dot above the
letter. It looks fine in most fonts right up until it doesn't.

Every case conversion in this project must go through this module.
Bare ``.upper()`` / ``.lower()`` on transcript or caption text is a bug.
"""

from __future__ import annotations

import unicodedata

# Translate the four letters whose casing differs from the invariant rules
# BEFORE delegating to Python for everything else. Doing it in this order
# is what prevents the combining-dot form from ever being produced.
_TO_UPPER = str.maketrans({"i": "İ", "ı": "I"})
_TO_LOWER = str.maketrans({"I": "ı", "İ": "i"})

# Letters that carry a Turkish diacritic, and their ASCII shadows. Used by
# the transcript repair pass to spot words that lost their diacritics.
DIACRITICS = "çÇğĞıİöÖşŞüÜ"
ASCII_SHADOWS = {"c": "ç", "g": "ğ", "i": "ı", "o": "ö", "s": "ş", "u": "ü"}

# Combining marks that must never survive normalization. Whisper's
# tokenizer can emit "s" + U+0327 instead of "ş"; both render acceptably
# but only one compares equal to the word in a dictionary.
_FORBIDDEN_COMBINING = ("\u0307", "\u0327", "\u0306", "\u0308")


def nfc(text: str) -> str:
    """Normalize to composed form. Apply at every ingest boundary."""
    return unicodedata.normalize("NFC", text)


def tr_upper(text: str) -> str:
    """Uppercase using Turkish rules (i -> İ, ı -> I)."""
    return nfc(text).translate(_TO_UPPER).upper()


def tr_lower(text: str) -> str:
    """Lowercase using Turkish rules (I -> ı, İ -> i, no combining dot)."""
    return nfc(text).translate(_TO_LOWER).lower()


def tr_title(text: str) -> str:
    """Title-case each whitespace-separated word using Turkish rules."""
    out = []
    for word in nfc(text).split(" "):
        if not word:
            out.append(word)
            continue
        out.append(tr_upper(word[:1]) + tr_lower(word[1:]))
    return " ".join(out)


def has_forbidden_combining(text: str) -> bool:
    """True if decomposed marks survived, meaning NFC was bypassed somewhere."""
    return any(mark in text for mark in _FORBIDDEN_COMBINING)


def diacritic_density(text: str) -> float:
    """Turkish diacritics per 1000 characters.

    Running Turkish text sits around 60-90. A transcript coming in far
    below that has been systematically ASCII-folded, which is an upstream
    problem (wrong model, wrong language hint) and cannot be repaired
    token by token -- so the caller should fail loudly rather than patch.
    """
    if not text:
        return 0.0
    hits = sum(1 for ch in text if ch in DIACRITICS)
    return hits * 1000.0 / len(text)


def ascii_fold(text: str) -> str:
    """Map Turkish letters to their ASCII shadows (for lookup keys only)."""
    table = str.maketrans("çÇğĞıİöÖşŞüÜ", "cCgGiIoOsSuU")
    return nfc(text).translate(table)
