"""Metrics collectors: honest absence, and correct maths on real shapes.

A collector's most common runtime state is "no credentials", and the
property that matters there is that it degrades to an empty result with a
stated reason rather than raising or -- far worse -- inventing values.
The arithmetic tests use the real response shape of the YouTube Analytics
API so a change in the mapping is caught without a network call.
"""

from __future__ import annotations

import pytest

from yvc.metrics.collectors import collector_status, get_collector
from yvc.metrics.collectors.base import CollectorResult
from yvc.metrics.collectors.youtube import (
    YouTubeCollector,
    _interpolate,
    _merge,
    _parse_date,
)

CREDENTIAL_VARS = [
    "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN",
    "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
]


@pytest.fixture
def no_credentials(monkeypatch):
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)


def test_missing_credentials_is_reported_not_raised(no_credentials):
    usable, reason = YouTubeCollector().credentials_status()
    assert not usable
    assert "YT_CLIENT_ID" in reason


def test_fetch_without_credentials_returns_empty_with_a_note(no_credentials):
    result = YouTubeCollector().fetch(
        remote_id="abc", window="T+24h",
        published_at_utc="2026-08-19T09:00:00Z", duration_s=45.0,
    )
    assert result.is_empty
    assert result.notes and "unavailable" in result.notes[0]
    assert not result.errors  # absent credentials is not an error


def test_fetch_without_a_remote_id_is_a_note_not_an_error(monkeypatch):
    for name, value in (
        ("YT_CLIENT_ID", "a"), ("YT_CLIENT_SECRET", "b"), ("YT_REFRESH_TOKEN", "c")
    ):
        monkeypatch.setenv(name, value)
    result = YouTubeCollector().fetch(
        remote_id="", window="T+24h",
        published_at_utc="2026-08-19T09:00:00Z", duration_s=45.0,
    )
    assert result.is_empty
    assert "no remote video id" in result.notes[0]


def test_sub_daily_window_is_refused_rather_than_approximated(monkeypatch):
    """YouTube Analytics is day-granular. Reporting a day as an hour would
    be a silent lie, so T+1h must fall through to simulation."""
    for name, value in (
        ("YT_CLIENT_ID", "a"), ("YT_CLIENT_SECRET", "b"), ("YT_REFRESH_TOKEN", "c")
    ):
        monkeypatch.setenv(name, value)
    result = YouTubeCollector().fetch(
        remote_id="abc", window="T+1h",
        published_at_utc="2026-08-19T09:00:00Z", duration_s=45.0,
    )
    assert result.is_empty
    assert "granularity" in result.notes[0]


# --- retention arithmetic -------------------------------------------

# Real shape: [elapsedVideoTimeRatio, audienceWatchRatio]
CURVE = [[0.0, 1.0], [0.05, 0.8], [0.10, 0.7], [0.50, 0.4], [1.0, 0.25]]


def test_interpolate_between_samples():
    # Midway between 0.05 (0.8) and 0.10 (0.7).
    assert _interpolate(CURVE, 0.075) == pytest.approx(0.75)


def test_interpolate_hits_exact_samples():
    assert _interpolate(CURVE, 0.05) == pytest.approx(0.8)
    assert _interpolate(CURVE, 0.0) == pytest.approx(1.0)


def test_interpolate_clamps_outside_the_range():
    assert _interpolate(CURVE, -1.0) == pytest.approx(1.0)
    assert _interpolate(CURVE, 5.0) == pytest.approx(0.25)
    assert _interpolate([], 0.5) is None


def test_three_second_ratio_depends_on_clip_duration():
    """3 s of a 60 s clip is 5% elapsed; of a 20 s clip it is 15%. Using a
    fixed ratio would misread the hook window on every clip but one."""
    assert _interpolate(CURVE, 3.0 / 60.0) == pytest.approx(0.8)
    assert _interpolate(CURVE, 3.0 / 20.0) < 0.8


# --- failure isolation ----------------------------------------------


def test_one_failing_call_does_not_lose_the_others():
    result = CollectorResult()
    values: dict = {}
    _merge(result, values, "good", lambda: {"views": 10})
    _merge(result, values, "bad", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    _merge(result, values, "also good", lambda: {"likes": 2})
    assert values == {"views": 10, "likes": 2}
    assert len(result.errors) == 1
    assert "bad: RuntimeError: boom" in result.errors[0]


def test_earlier_values_are_not_overwritten():
    """Windowed metrics must win over lifetime counters."""
    result = CollectorResult()
    values: dict = {}
    _merge(result, values, "windowed", lambda: {"likes": 5})
    _merge(result, values, "lifetime", lambda: {"likes": 999})
    assert values["likes"] == 5


# --- dates and registry ---------------------------------------------


def test_parse_date_handles_z_suffix_and_naive_input():
    assert _parse_date("2026-08-19T09:00:00Z") is not None
    assert _parse_date("2026-08-19T09:00:00").tzinfo is not None
    assert _parse_date("not a date") is None
    assert _parse_date("") is None


def test_registry_returns_none_without_credentials(no_credentials):
    assert get_collector("youtube") is None


def test_unimplemented_platforms_state_why():
    for platform in ("instagram", "tiktok", "linkedin", "x"):
        usable, reason = collector_status(platform)
        assert not usable
        assert "no collector" in reason, f"{platform} gives no reason"
        assert get_collector(platform) is None


def test_unknown_platform_does_not_raise():
    usable, reason = collector_status("myspace")
    assert not usable and reason


def test_credentials_exported_after_construction_are_seen(monkeypatch):
    """The registry caches instances, so credentials must be read on use."""
    collector = YouTubeCollector()
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    assert not collector.credentials_status()[0]

    for name, value in (
        ("YT_CLIENT_ID", "a"), ("YT_CLIENT_SECRET", "b"), ("YT_REFRESH_TOKEN", "c")
    ):
        monkeypatch.setenv(name, value)
    assert collector.credentials_status()[0]
