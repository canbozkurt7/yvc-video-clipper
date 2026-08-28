"""The copy and the publisher have to agree on what the post is.

They did not. Copywriting was told to end the tweet with the link, and
the X adapter appends `req.tracking_url` to whatever text it is given --
so the link went out twice and the tweet ran 313 characters against a
280 limit. The overflow surfaced in the publish dry run, which is the
one place in the pipeline where nothing can be rewritten.
"""

from __future__ import annotations

import pytest

from yvc.publish.adapters import _urls_as_23, get_adapter
from yvc.publish.base import MediaAsset, PublishRequest
from yvc.stages.s09_copywrite import (
    PLATFORM_SPECS,
    ClipCopy,
    PlatformCopy,
    body_budget,
    validate_copy,
)

CLIP_TEXT = (
    "Asgari ücretin toplam ücretler içindeki payı yüzde altmışların "
    "üzerinde ve bu oran daha önce bu kadar yüksek değildi hiç."
)
QUOTE = "Asgari ücretin toplam ücretler içindeki payı yüzde altmışların üzerinde"
LINK = (
    "https://www.datassist.com.tr/?utm_source=x&utm_medium=social_organic"
    "&utm_campaign=datassist_clips&utm_content=c04&utm_term=data_number"
)


def _copy(**bodies) -> ClipCopy:
    def block(platform):
        return PlatformCopy(
            body=bodies.get(platform, "Kısa ve temiz bir metin."),
            hashtags=["#Bordro"] if platform == "x" else ["#a", "#b", "#c"],
            cta="oku",
        )

    return ClipCopy(
        linkedin=block("linkedin"), instagram=block("instagram"),
        x=block("x"), tiktok=block("tiktok"), youtube=block("youtube"),
        evidence_quote=QUOTE, angle="rakam",
    )


def test_x_reserves_room_for_the_link_the_publisher_appends():
    assert body_budget(PLATFORM_SPECS["x"]) == 256


def test_platforms_with_room_to_spare_keep_their_full_limit():
    assert body_budget(PLATFORM_SPECS["linkedin"]) == 3000


@pytest.mark.parametrize("platform", ["x", "linkedin", "youtube"])
def test_a_link_in_the_body_is_an_error_where_the_publisher_adds_one(platform):
    issues = validate_copy(_copy(**{platform: f"Bir iddia. {LINK}"}), CLIP_TEXT, [])

    codes = {(i["code"], i["severity"]) for i in issues}
    assert ("LINK_IN_BODY", "error") in codes


def test_instagram_may_mention_a_url_because_nothing_is_appended_there():
    issues = validate_copy(_copy(instagram=f"Profildeki link: {LINK}"), CLIP_TEXT, [])

    assert not [i for i in issues if i["code"] == "LINK_IN_BODY"]


def test_a_tweet_that_only_fits_without_the_appended_link_is_rejected():
    # 260 characters: under 280, over the 256 the body actually has.
    issues = validate_copy(_copy(x="a" * 260), CLIP_TEXT, [])

    over = [i for i in issues if i["code"] == "LEN_OVER"]
    assert over and "256" in over[0]["detail"]


def _request(text: str) -> PublishRequest:
    return PublishRequest(
        post_id="c04-x-A", clip_id="c04", variant="A", platform="x", text=text,
        media=MediaAsset(path="clip.mp4", mime="video/mp4", bytes=1024,
                         duration_s=90.0, width=1920, height=1080, aspect="16:9"),
        tracking_url=LINK,
    )


def test_the_adapter_counts_a_url_in_the_body_the_way_x_does():
    assert len(_urls_as_23(f"Bir iddia. {LINK}")) == len("Bir iddia. ") + 23


def test_a_body_at_the_budget_passes_the_adapter_with_the_link_appended():
    issues = get_adapter("x").validate(_request("a" * 256))

    assert not [i for i in issues if i.code == "TEXT_TOO_LONG"]


def test_one_character_more_does_not():
    issues = get_adapter("x").validate(_request("a" * 257))

    assert [i for i in issues if i.code == "TEXT_TOO_LONG"]
