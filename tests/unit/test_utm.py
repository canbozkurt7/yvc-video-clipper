"""UTM build/parse round-trip.

If these drift apart, attribution fails silently: links still resolve and
traffic still arrives, but the hook analysis loses its click term without
any error surfacing. That makes a round-trip property test unusually
valuable here relative to its cost.
"""

from __future__ import annotations

import itertools

import pytest

from yvc.attribution.utm import build, campaign_name, parse, rows_for_export, slug

DESTINATION = "https://www.datassist.com.tr/"

PLATFORMS = ["linkedin", "instagram", "x", "tiktok", "youtube"]
HOOKS = ["contrarian", "data_number", "curiosity_gap", "social_proof"]
CLIPS = ["c01", "c12", "r39OrneyMDs-c03"]
VARIANTS = ["A", "B"]


@pytest.mark.parametrize(
    "platform,hook,clip,variant",
    list(itertools.product(PLATFORMS, HOOKS, CLIPS, VARIANTS))[:40],
)
def test_build_parse_roundtrip(platform, hook, clip, variant):
    url = build(
        DESTINATION, platform=platform, clip_id=clip, hook_type=hook,
        variant=variant, campaign="datassist_clips_202608", run="9f3a1c",
    )
    key = parse(url)
    assert key is not None
    assert key.platform == platform
    assert key.clip_id == slug(clip)
    assert key.hook_type == hook
    assert key.variant == variant.lower()


def test_untagged_url_returns_none():
    assert parse("https://www.datassist.com.tr/bordro") is None


def test_existing_query_parameters_are_preserved():
    url = build(
        "https://example.com/x?ref=partner&lang=tr",
        platform="linkedin", clip_id="c01", hook_type="contrarian",
    )
    assert "ref=partner" in url
    assert "lang=tr" in url
    assert parse(url).hook_type == "contrarian"


def test_turkish_characters_are_slugged_not_dropped():
    # Hook types are ASCII by construction, but clip ids and campaigns can
    # pick up Turkish text; it must not break the query string.
    url = build(
        DESTINATION, platform="instagram", clip_id="kesit-ücret",
        hook_type="data_number", campaign="maaş_zam",
    )
    key = parse(url)
    assert key is not None
    assert " " not in url
    assert key.hook_type == "data_number"


def test_campaign_name_is_stable_and_safe():
    name = campaign_name("Datassist", "r39OrneyMDs", "202608")
    assert name == campaign_name("Datassist", "r39OrneyMDs", "202608")
    assert " " not in name and name == name.lower()


def test_rows_for_export_recovers_hook_type():
    posts = [{
        "post_id": "c01-linkedin-A", "clip_id": "c01", "platform": "linkedin",
        "variant": "A",
        "tracking_url": build(
            DESTINATION, platform="linkedin", clip_id="c01",
            hook_type="contrarian", campaign="c",
        ),
    }]
    rows = rows_for_export(posts)
    assert rows[0]["hook_type"] == "contrarian"
