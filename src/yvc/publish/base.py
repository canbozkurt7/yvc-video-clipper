"""Publishing adapters: one code path for dry-run and live.

The design goal is that going live requires **no code change** -- only
credentials in ``.env``. That is achieved by splitting each adapter in
two:

    build_calls(request) -> list[HttpCall]      pure, no network
    execute(calls)       -> PublishResult       network only

``DryRunAdapter`` calls ``build_calls`` and writes the result to disk.
``LiveAdapter`` calls ``build_calls`` and then ``execute``. Because both
share the same builder, the dry-run artifacts are the exact requests that
would be sent -- not mocks that can drift from reality. The contract
tests snapshot ``build_calls`` output, so a change to the live path that
does not also change the dry-run proof is caught.

Secrets are redacted in serialised output but their *shape* is preserved,
so a reviewer can confirm the Authorization header exists and is well
formed without the file carrying a usable token.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from yvc.io import write_json, write_text

REDACTED = "***REDACTED***"


@dataclass(frozen=True)
class MediaAsset:
    path: str
    mime: str
    bytes: int
    duration_s: float
    width: int
    height: int
    aspect: str
    public_url: str | None = None


@dataclass(frozen=True)
class PublishRequest:
    post_id: str
    clip_id: str
    variant: str
    platform: str
    text: str
    media: MediaAsset
    hashtags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    tracking_url: str = ""
    title: str | None = None
    scheduled_at_utc: str | None = None

    @property
    def idempotency_key(self) -> str:
        return self.post_id


@dataclass
class HttpCall:
    seq: int
    label: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    body: Any = None
    body_kind: Literal["json", "form", "multipart", "binary", "none"] = "none"
    would_send_bytes: int = 0
    note: str | None = None


@dataclass
class ValidationIssue:
    code: str
    severity: Literal["error", "warn"]
    detail: str


@dataclass
class PublishResult:
    post_id: str
    platform: str
    status: Literal["published", "scheduled", "dry_run", "failed", "skipped_no_creds"]
    mode: str
    calls: list[HttpCall] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    remote_id: str | None = None
    permalink: str | None = None
    error: str | None = None


class PublisherAdapter(Protocol):
    platform: str
    supports_native_schedule: bool
    requires_public_media_url: bool
    max_text_chars: int
    accepted_aspects: frozenset[str]

    def credential_names(self) -> list[str]: ...
    def validate(self, req: PublishRequest) -> list[ValidationIssue]: ...
    def build_calls(self, req: PublishRequest) -> list[HttpCall]: ...


class BaseAdapter:
    """Shared validation and credential handling."""

    platform = "base"
    supports_native_schedule = False
    requires_public_media_url = False
    max_text_chars = 3000
    max_media_bytes = 512 * 1024 * 1024
    max_duration_s = 600.0
    accepted_aspects = frozenset({"9:16", "16:9", "1:1"})

    def credential_names(self) -> list[str]:
        return []

    def credentials_present(self) -> bool:
        names = self.credential_names()
        return bool(names) and all(os.environ.get(n) for n in names)

    def missing_credentials(self) -> list[str]:
        return [n for n in self.credential_names() if not os.environ.get(n)]

    def _secret(self, name: str) -> str:
        """Return the real value if present, else a shaped placeholder.

        A placeholder keeps the payload structurally complete in dry-run
        so the request is reviewable end to end.
        """
        return os.environ.get(name) or f"<{name}>"

    def validate(self, req: PublishRequest) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        length = len(req.text)
        if length > self.max_text_chars:
            issues.append(
                ValidationIssue(
                    "TEXT_TOO_LONG", "error",
                    f"{length} chars exceeds {self.platform} limit of {self.max_text_chars}",
                )
            )
        if req.media.aspect not in self.accepted_aspects:
            issues.append(
                ValidationIssue(
                    "ASPECT_UNSUPPORTED", "warn",
                    f"{req.media.aspect} is unusual for {self.platform}",
                )
            )
        if req.media.bytes > self.max_media_bytes:
            issues.append(
                ValidationIssue(
                    "MEDIA_TOO_LARGE", "error",
                    f"{req.media.bytes} bytes exceeds {self.max_media_bytes}",
                )
            )
        if req.media.duration_s > self.max_duration_s:
            issues.append(
                ValidationIssue(
                    "DURATION_TOO_LONG", "error",
                    f"{req.media.duration_s}s exceeds {self.max_duration_s}s",
                )
            )
        if self.requires_public_media_url and not req.media.public_url:
            issues.append(
                ValidationIssue(
                    "PUBLIC_URL_REQUIRED", "error",
                    f"{self.platform} cannot accept a local file; a public HTTPS URL "
                    "is required (configure a MediaHost)",
                )
            )
        if req.scheduled_at_utc and not self.supports_native_schedule:
            issues.append(
                ValidationIssue(
                    "NO_NATIVE_SCHEDULE", "warn",
                    f"{self.platform} has no scheduling API; the local queue will "
                    "hold this until due",
                )
            )
        return issues

    def build_calls(self, req: PublishRequest) -> list[HttpCall]:  # pragma: no cover
        raise NotImplementedError


def _sanitise_value(value: Any, env_names: list[str]) -> Any:
    """Replace any credential value with its ``<ENV_NAME>`` placeholder.

    Applied recursively to request bodies, not just headers. Credentials
    turn up in payloads as well -- LinkedIn puts the organisation URN in
    the body, and several platforms carry account ids there. Redacting
    only headers would leave the proof artifact carrying live values.
    """
    if isinstance(value, str):
        for name in env_names:
            actual = os.environ.get(name)
            if actual and actual in value:
                value = value.replace(actual, f"<{name}>")
        return value
    if isinstance(value, dict):
        return {k: _sanitise_value(v, env_names) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitise_value(v, env_names) for v in value]
    return value


def _redact(headers: dict[str, str]) -> dict[str, str]:
    """Hide secret values while preserving the header's shape."""
    out = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in {"authorization", "x-api-key", "profile-key", "cookie"}:
            if value.startswith("Bearer "):
                out[key] = f"Bearer {REDACTED}"
            else:
                out[key] = REDACTED
        else:
            out[key] = value
    return out


class DryRunAdapter:
    """Wraps an adapter and serialises what it would send.

    The artifacts written here are the deliverable's publish proof. They
    are produced by the same ``build_calls`` the live path uses, so they
    cannot drift from real behaviour.
    """

    def __init__(self, inner: BaseAdapter, out_dir: Path):
        self.inner = inner
        self.out_dir = out_dir
        self.platform = inner.platform
        self.mode = "dry_run"

    def publish(self, req: PublishRequest) -> PublishResult:
        issues = self.inner.validate(req)
        calls = self.inner.build_calls(req)

        target = self.out_dir / self.inner.platform / req.post_id
        target.mkdir(parents=True, exist_ok=True)

        env_names = self.inner.credential_names()
        for call in calls:
            payload = asdict(call)
            payload["headers"] = _sanitise_value(
                _redact(call.headers), env_names
            )
            payload["body"] = _sanitise_value(call.body, env_names)
            payload["url"] = _sanitise_value(call.url, env_names)
            write_json(target / f"{call.seq:02d}_{call.label}.request.json", payload)

        write_text(target / "curl.sh", _as_curl(calls, self.inner))

        result = PublishResult(
            post_id=req.post_id,
            platform=self.inner.platform,
            status="dry_run",
            mode="dry_run",
            calls=calls,
            issues=issues,
        )
        summary = asdict(result)
        for call in summary["calls"]:
            call["headers"] = _sanitise_value(_redact(call["headers"]), env_names)
            call["body"] = _sanitise_value(call["body"], env_names)
            call["url"] = _sanitise_value(call["url"], env_names)
        write_json(target / "summary.json", summary)
        return result


def _as_curl(calls: list[HttpCall], adapter: "BaseAdapter | None" = None) -> str:
    """Replayable curl script with environment placeholders for secrets.

    Header values are rewritten so a real credential is replaced by the
    shell expansion that would supply it ("$LINKEDIN_ACCESS_TOKEN").
    Writing the resolved value here would put live secrets on disk inside
    an artifact meant to be reviewable and committable -- the whole point
    of the publish proof is that it can be shared.
    """
    env_names = list(adapter.credential_names()) if adapter else []

    def sanitise(value: str) -> str:
        for name in env_names:
            actual = os.environ.get(name)
            if actual and actual in value:
                value = value.replace(actual, f"${name}")
        return value

    lines = [
        "#!/usr/bin/env bash",
        "# Generated by yvc. Secrets are referenced as environment variables,",
        "# never inlined, so this script is safe to commit and can be replayed",
        "# once .env is exported.",
        "set -euo pipefail",
        "",
    ]
    if env_names:
        lines.append("# Required: " + ", ".join(env_names))
        lines.append("")
    for call in calls:
        parts = [f"curl -X {call.method} '{sanitise(call.url)}'"]
        for key, value in call.headers.items():
            parts.append(f"  -H '{key}: {sanitise(value)}'")
        if call.body_kind == "json" and call.body is not None:
            body = sanitise(json.dumps(call.body, ensure_ascii=False))
            parts.append(f"  --data-raw '{body}'")
        elif call.body_kind == "binary":
            parts.append("  --data-binary @<file>")
        lines.append(f"# [{call.seq}] {call.label}")
        if call.note:
            lines.append(f"# {call.note}")
        lines.append(" \\\n".join(parts))
        lines.append("")
    return "\n".join(lines)
