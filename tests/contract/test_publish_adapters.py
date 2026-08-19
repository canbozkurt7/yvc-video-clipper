"""Contract tests for publish adapters.

The project's central publishing claim is that dry-run artifacts are the
exact requests the live path would send, so switching to live needs no
code change. That claim only holds while `build_calls()` stays the single
shared builder. These tests pin its output.

They also assert the properties that make the dry-run proof trustworthy:
secrets never appear in serialised output, credentials are absent rather
than faked, and each platform's real multi-step upload sequence is
present in the right order.
"""

from __future__ import annotations

import json

import pytest

from yvc.publish.adapters import REGISTRY, get_adapter
from yvc.publish.base import DryRunAdapter, MediaAsset, PublishRequest

SECRET_MARKERS = ["ghp_", "Bearer sk-", "AKIA", "-----BEGIN"]


def make_request(platform: str, aspect: str = "9:16", duration: float = 38.0):
    return PublishRequest(
        post_id=f"c01-{platform}-A",
        clip_id="c01",
        variant="A",
        platform=platform,
        text="Asgari ücretin yüzde kırkı vergiye gidiyor. Peki bu para nereye?",
        media=MediaAsset(
            path="work/x/clips/c01/clip.mp4", mime="video/mp4",
            bytes=9_400_000, duration_s=duration,
            width=1080 if aspect == "9:16" else 1920,
            height=1920 if aspect == "9:16" else 1080,
            aspect=aspect,
            public_url="https://cdn.example.com/c01-A.mp4",
        ),
        hashtags=["#bordro", "#İK", "#maaş"],
        tracking_url="https://www.datassist.com.tr/?utm_source=linkedin",
        title="Maaşın yüzde kırkı nereye gidiyor?",
        scheduled_at_utc="2026-08-19T05:41:00Z",
    )


@pytest.mark.parametrize("platform", sorted(REGISTRY))
def test_build_calls_is_pure_and_repeatable(platform):
    """Two builds must be byte-identical: no timestamps, no randomness."""
    adapter = get_adapter(platform)
    request = make_request(platform, aspect=_aspect_for(adapter))
    first = adapter.build_calls(request)
    second = adapter.build_calls(request)
    assert _serialise(first) == _serialise(second)


@pytest.mark.parametrize("platform", sorted(REGISTRY))
def test_calls_are_sequential_and_well_formed(platform):
    adapter = get_adapter(platform)
    calls = adapter.build_calls(make_request(platform, aspect=_aspect_for(adapter)))

    assert calls, "adapter produced no calls"
    assert [c.seq for c in calls] == list(range(1, len(calls) + 1))
    for call in calls:
        assert call.method in {"GET", "POST", "PUT", "PATCH"}
        assert call.url
        assert call.label


@pytest.mark.parametrize("platform", sorted(REGISTRY))
def test_no_real_secrets_leak_into_serialised_output(platform, tmp_path, monkeypatch):
    """Even with credentials set, the written proof must redact them."""
    for name in get_adapter(platform).credential_names():
        monkeypatch.setenv(name, "SUPER_SECRET_VALUE_12345")

    adapter = get_adapter(platform)
    request = make_request(platform, aspect=_aspect_for(adapter))
    DryRunAdapter(adapter, tmp_path).publish(request)

    written = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    assert "SUPER_SECRET_VALUE_12345" not in written
    for marker in SECRET_MARKERS:
        assert marker not in written


@pytest.mark.parametrize("platform", sorted(REGISTRY))
def test_missing_credentials_are_reported_not_faked(platform, monkeypatch):
    for name in get_adapter(platform).credential_names():
        monkeypatch.delenv(name, raising=False)
    adapter = get_adapter(platform)
    missing = adapter.missing_credentials()
    assert missing == adapter.credential_names()
    assert not adapter.credentials_present()


def test_linkedin_upload_sequence():
    calls = get_adapter("linkedin").build_calls(make_request("linkedin", "16:9", 95))
    labels = [c.label for c in calls]
    assert labels[0] == "initialize_upload"
    assert any(l.startswith("upload_part_") for l in labels)
    assert "finalize_upload" in labels
    assert labels[-1] == "create_post"
    # Part count must follow the file size, not be hardcoded.
    parts = [c for c in calls if c.label.startswith("upload_part_")]
    assert len(parts) == -(-9_400_000 // (4 * 1024 * 1024))


def test_instagram_is_container_then_publish_and_needs_public_url():
    adapter = get_adapter("instagram")
    assert adapter.requires_public_media_url
    labels = [c.label for c in adapter.build_calls(make_request("instagram"))]
    assert labels == [
        "check_publishing_limit", "create_container",
        "poll_container_status", "publish_container",
    ]

    # A local-only asset must be rejected, since Graph cannot fetch it.
    request = make_request("instagram")
    local = PublishRequest(
        **{**request.__dict__, "media": MediaAsset(
            path=request.media.path, mime="video/mp4", bytes=1000,
            duration_s=30, width=1080, height=1920, aspect="9:16",
            public_url=None,
        )}
    )
    codes = [i.code for i in adapter.validate(local)]
    assert "PUBLIC_URL_REQUIRED" in codes


def test_youtube_is_the_only_native_scheduler():
    native = {p for p in REGISTRY if get_adapter(p).supports_native_schedule}
    assert native == {"youtube"}

    calls = get_adapter("youtube").build_calls(make_request("youtube"))
    body = calls[0].body
    assert body["status"]["publishAt"] == "2026-08-19T05:41:00Z"
    assert body["status"]["privacyStatus"] == "private"


def test_youtube_warns_about_quota_cost():
    codes = [i.code for i in get_adapter("youtube").validate(make_request("youtube"))]
    assert "QUOTA_COST" in codes


def test_tiktok_surfaces_the_audit_gate():
    adapter = get_adapter("tiktok")
    codes = [i.code for i in adapter.validate(make_request("tiktok"))]
    assert "TIKTOK_UNAUDITED" in codes
    # creator_info must be first: TikTok requires it before publishing.
    assert adapter.build_calls(make_request("tiktok"))[0].label == "query_creator_info"


def test_x_counts_a_url_as_23_characters():
    adapter = get_adapter("x")
    request = make_request("x")
    long_text = PublishRequest(**{**request.__dict__, "text": "a" * 270})
    codes = [i.code for i in adapter.validate(long_text)]
    assert "TEXT_TOO_LONG" in codes, "270 chars + a URL must exceed 280"


def test_aspect_mismatch_is_flagged():
    # A horizontal clip sent to Instagram Reels should not pass silently.
    codes = [
        i.code for i in get_adapter("instagram").validate(
            make_request("instagram", aspect="16:9")
        )
    ]
    assert "ASPECT_UNSUPPORTED" in codes


def test_dry_run_writes_replayable_proof(tmp_path):
    adapter = get_adapter("linkedin")
    DryRunAdapter(adapter, tmp_path).publish(make_request("linkedin", "16:9", 95))
    target = tmp_path / "linkedin" / "c01-linkedin-A"
    assert (target / "curl.sh").exists()
    assert (target / "summary.json").exists()
    assert list(target.glob("*.request.json"))

    curl = (target / "curl.sh").read_text(encoding="utf-8")
    assert curl.startswith("#!/usr/bin/env bash")
    assert "api.linkedin.com" in curl


def _aspect_for(adapter) -> str:
    return "9:16" if "9:16" in adapter.accepted_aspects else "16:9"


def _serialise(calls) -> str:
    return json.dumps(
        [
            {
                "seq": c.seq, "label": c.label, "method": c.method,
                "url": c.url, "headers": c.headers, "query": c.query,
                "body": c.body, "body_kind": c.body_kind,
            }
            for c in calls
        ],
        sort_keys=True, ensure_ascii=False,
    )
