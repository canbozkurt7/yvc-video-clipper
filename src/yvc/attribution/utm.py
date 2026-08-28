"""UTM tagging and its exact inverse.

``utm_term`` carries the hook type. That single choice is what lets click
and conversion data join straight back to hook performance without a
separate mapping table -- an inbound analytics row can be attributed to
``(clip_id, variant, hook_type)`` by parsing the URL alone.

``build`` and ``parse`` are inverses, enforced by a property test. If they
ever drift, attribution silently stops working: links keep resolving,
traffic keeps arriving, and the hook analysis quietly loses its click
term. A round-trip test is cheap insurance against a failure that would
otherwise be invisible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Kept conservative: these survive URL encoding and analytics tooling
# without normalisation surprises.
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class AttributionKey:
    platform: str
    clip_id: str
    hook_type: str
    variant: str
    campaign: str
    run: str = ""
    # The same clip ships a Turkish and an English post on LinkedIn, so a
    # click is only attributable if the language is part of the key. It
    # lives here rather than only in the emitted URL because `parse` is
    # the documented way back from a click to a decision, and a parameter
    # this key cannot represent is a parameter the analysis silently loses.
    lang: str = "tr"

    def as_params(self) -> dict[str, str]:
        return {
            "utm_source": self.platform,
            "utm_medium": "social_organic",
            "utm_campaign": self.campaign,
            "utm_content": self.clip_id,
            "utm_term": self.hook_type,
            "yvc_v": self.variant,
            "yvc_lang": self.lang,
            "yvc_run": self.run,
        }


def slug(value: str) -> str:
    """Reduce a string to characters that survive a query string intact."""
    cleaned = _SAFE.sub("-", (value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned.lower()


def build(
    destination: str,
    *,
    platform: str,
    clip_id: str,
    hook_type: str,
    variant: str = "A",
    campaign: str = "clips",
    run: str = "",
    lang: str = "tr",
) -> str:
    """Append UTM parameters to a destination URL.

    Existing query parameters on the destination are preserved; only the
    UTM keys are set, so a destination that already carries its own
    parameters is not clobbered.
    """
    key = AttributionKey(
        platform=slug(platform),
        clip_id=slug(clip_id),
        hook_type=slug(hook_type),
        variant=slug(variant) or "a",
        campaign=slug(campaign),
        run=slug(run),
        lang=slug(lang) or "tr",
    )

    parts = urlparse(destination)
    existing = parse_qs(parts.query, keep_blank_values=True)
    merged = {k: v[0] for k, v in existing.items()}
    merged.update({k: v for k, v in key.as_params().items() if v})

    return urlunparse(parts._replace(query=urlencode(merged)))


def parse(url: str) -> AttributionKey | None:
    """Recover the attribution key from a tagged URL.

    Returns None when the URL carries no UTM tags at all, so callers can
    distinguish untagged traffic from malformed tagging.
    """
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    if "utm_source" not in query:
        return None

    def first(name: str, default: str = "") -> str:
        values = query.get(name)
        return values[0] if values else default

    return AttributionKey(
        platform=first("utm_source"),
        clip_id=first("utm_content"),
        hook_type=first("utm_term"),
        variant=first("yvc_v", "a"),
        campaign=first("utm_campaign"),
        run=first("yvc_run"),
        lang=first("yvc_lang", "tr"),
    )


def campaign_name(brand: str, video_id: str, yyyymm: str) -> str:
    """Stable campaign identifier: brand, month, source video."""
    return slug(f"{brand}_clips_{yyyymm}_{video_id}")


def rows_for_export(posts: list[dict]) -> list[dict]:
    """Flatten tagged posts into attribution.csv rows.

    Emitted every run, so the attribution scheme is a usable deliverable
    even before a single click has been recorded.
    """
    out = []
    for post in posts:
        key = parse(post.get("tracking_url", "") or "")
        out.append({
            "post_id": post.get("post_id"),
            "clip_id": post.get("clip_id"),
            "platform": post.get("platform"),
            "variant": post.get("variant", "A"),
            # Read back off the URL, not just copied from the post: this
            # column is what proves the tag actually survived into the
            # link a viewer will click.
            "lang": (key.lang if key else post.get("lang")) or "tr",
            "hook_type": key.hook_type if key else "",
            "campaign": key.campaign if key else "",
            "url": post.get("tracking_url", ""),
            "scheduled_at_utc": post.get("scheduled_at_utc", ""),
        })
    return out
