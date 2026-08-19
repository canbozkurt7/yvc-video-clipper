"""Collector interface: real platform metrics, or an honest absence.

A collector never fabricates. It returns only the fields a platform
actually gave back, and the simulator fills the rest -- which is what
makes the per-field provenance in the report true rather than decorative.

Two failure modes are deliberately *not* errors, because neither should
end a run:

* no credentials -- the collector reports itself unavailable and the row
  becomes fully SIMULATED;
* a platform returning partial data -- the row becomes MIXED, and the
  detail records which fields were real.

Only an unexpected API failure is recorded as an error, and even then the
run continues with simulated values plus a visible note.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class CollectorResult:
    """What a platform actually returned."""

    values: dict = field(default_factory=dict)
    source: str = ""
    fetched_at: str = ""
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.values


@runtime_checkable
class Collector(Protocol):
    platform: str

    def credentials_status(self) -> tuple[bool, str]:
        """(usable, human-readable reason)."""

    def fetch(
        self, *, remote_id: str, window: str, published_at_utc: str, duration_s: float
    ) -> CollectorResult: ...


def env(*names: str) -> str | None:
    """First non-empty environment variable among `names`.

    Several names are accepted per credential because the obvious name
    differs between Google's own docs and most tutorials, and a silently
    unread credential is a miserable thing to debug.
    """
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def tls_verify() -> object:
    """CA configuration for outbound HTTPS.

    This machine sits behind a TLS-inspecting proxy, so the bundled
    certifi roots can legitimately fail against a re-signed certificate.
    `YVC_CA_BUNDLE` points at the corporate root; `YVC_INSECURE_TLS=1` is
    a last resort and says so out loud when used.
    """
    bundle = env("YVC_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")
    if bundle and os.path.exists(bundle):
        return bundle
    if env("YVC_INSECURE_TLS") in {"1", "true", "yes"}:
        print("[collect] WARNING TLS verification disabled via YVC_INSECURE_TLS")
        return False
    return True


# Window -> how many whole days after publication the window closes.
# YouTube Analytics is day-granular, so sub-day windows cannot be honoured
# and are reported as such instead of being silently rounded.
WINDOW_DAYS = {"T+1h": 0, "T+24h": 1, "T+7d": 7, "T+30d": 30}
