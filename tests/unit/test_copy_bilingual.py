"""Bonus: the LinkedIn transcreation (`body_en`) was generated and then
thrown away, since nothing downstream ever read it. `write_copy` now
publishes it as its own post when `bilingual` is on.

A stub LLM keeps this a test of the post-building logic, not of Claude.
"""

from __future__ import annotations

import json

from yvc.io import read_json, write_json
from yvc.stages.s09_copywrite import ClipCopy, PlatformCopy, write_copy

CLIP_TEXT = (
    "Bugun 30 milyon ucretlinin 18 milyonu asgari ucrete yakin calisiyor "
    "diye konusuluyor bu panelde surekli."
)
EVIDENCE = "Bugun 30 milyon ucretlinin 18 milyonu asgari ucrete yakin calisiyor"


class StubResult:
    def __init__(self, data):
        self.data = data


class StubLLM:
    """Always returns one fixed, valid ClipCopy -- with an English body."""

    def complete(self, task, prompt, schema, model=None, **kwargs):
        block = PlatformCopy(body="Turkce metin buraya yeterince uzun yazilir.",
                              hashtags=["#a", "#b", "#c"], cta="oku")
        return StubResult(ClipCopy(
            linkedin=block,
            instagram=PlatformCopy(body="kisa", hashtags=["#a"] * 9, cta="oku"),
            x=PlatformCopy(body="kisa", hashtags=["#a"], cta="oku"),
            tiktok=block, youtube=block,
            evidence_quote=EVIDENCE, key_number="18 milyon", angle="rakam",
            body_en="Here is the English transcreation of the LinkedIn post.",
        ))


def _setup(tmp_path):
    write_json(tmp_path / "clips.json", {"clips": [{
        "clip_id": "c01", "aspect": "9:16", "text": CLIP_TEXT,
        "hook_type": "data_number", "hook_line": "hook",
    }]})
    brand = {
        "name": "Test", "logo": {}, "fonts": {}, "colors": {},
        "voice": {"persona": "uzman", "audience": "genel"},
        "destination_url": "https://example.com/",
        "banned_phrases": [],
    }
    (tmp_path / "brand.json").write_text(json.dumps(brand), encoding="utf-8")
    return tmp_path / "clips.json", tmp_path / "posts.json", tmp_path / "brand.json"


def test_bilingual_true_adds_an_english_linkedin_post(tmp_path):
    clips_path, out_path, brand_path = _setup(tmp_path)
    write_copy(clips_path, out_path, brand_path=brand_path, llm=StubLLM(),
               bilingual=True)

    posts = read_json(out_path)["posts"]
    en_posts = [p for p in posts if p.get("lang") == "en"]
    assert len(en_posts) == 1
    en = en_posts[0]
    assert en["platform"] == "linkedin"
    assert en["text"] == "Here is the English transcreation of the LinkedIn post."
    assert en["post_id"] == "c01-linkedin-A-en"
    assert "yvc_lang=en" in en["tracking_url"]

    tr_linkedin = next(p for p in posts if p["platform"] == "linkedin" and p["lang"] == "tr")
    assert "yvc_lang=tr" in tr_linkedin["tracking_url"]


def test_bilingual_false_publishes_turkish_only(tmp_path):
    clips_path, out_path, brand_path = _setup(tmp_path)
    write_copy(clips_path, out_path, brand_path=brand_path, llm=StubLLM(),
               bilingual=False)

    posts = read_json(out_path)["posts"]
    assert not [p for p in posts if p.get("lang") == "en"]
    assert {p["platform"] for p in posts} == {
        "linkedin", "instagram", "x", "tiktok", "youtube",
    }


def test_english_post_is_never_created_for_x(tmp_path):
    """body_en is a LinkedIn-length transcreation, not an X-length draft --
    posting it to X's 280-char budget would just fail LEN_OVER."""
    clips_path, out_path, brand_path = _setup(tmp_path)
    write_copy(clips_path, out_path, brand_path=brand_path, llm=StubLLM(),
               bilingual=True)

    posts = read_json(out_path)["posts"]
    assert not [p for p in posts if p.get("lang") == "en" and p["platform"] == "x"]
