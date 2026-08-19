"""Collector registry.

Platforms resolve to a collector only if one exists *and* its credentials
are usable. Everything else returns None with a stated reason, which the
collect stage records so the report can distinguish "this platform has no
integration" from "this platform was not configured" from "this platform
returned nothing". Those are three different problems and flattening them
into a blank cell is how measurement gaps hide.
"""

from __future__ import annotations

from yvc.metrics.collectors.base import Collector, CollectorResult
from yvc.metrics.collectors.youtube import YouTubeCollector

__all__ = ["Collector", "CollectorResult", "collector_status", "get_collector"]

_BUILDERS = {
    "youtube": YouTubeCollector,
}

# Platforms with a publish adapter but no metrics collector yet. Named
# explicitly so the gap appears in the report instead of as silence.
_NOT_IMPLEMENTED = {
    "instagram": "no collector: Graph API insights integration not built",
    "tiktok": "no collector: Display API integration not built",
    "linkedin": "no collector: organizationalEntityShareStatistics not built",
    "x": "no collector: free tier allows ~100 reads/month, not built",
}

_CACHE: dict[str, object] = {}


def get_collector(platform: str) -> Collector | None:
    """A usable collector for `platform`, or None."""
    usable, _ = collector_status(platform)
    if not usable:
        return None
    if platform not in _CACHE:
        _CACHE[platform] = _BUILDERS[platform]()
    return _CACHE[platform]  # type: ignore[return-value]


def collector_status(platform: str) -> tuple[bool, str]:
    """(usable, reason) -- the reason is reported even on success."""
    builder = _BUILDERS.get(platform)
    if builder is None:
        return False, _NOT_IMPLEMENTED.get(platform, "no collector for this platform")
    if platform not in _CACHE:
        _CACHE[platform] = builder()
    return _CACHE[platform].credentials_status()  # type: ignore[attr-defined]
