"""Concrete platform adapters.

Each one builds the real request sequence for that platform's video
publishing flow. They are pure: no network, no side effects, so the same
code produces both the dry-run proof and the live requests.

Every endpoint shape here should be re-verified against current API docs
before going live. Platform APIs move (Meta renamed `plays`/`impressions`
to `views`; X migrated media upload from v1.1 to /2), and an adapter that
was right six months ago may not be right today. The places that matter
most are flagged inline.
"""

from __future__ import annotations

from yvc.publish.base import BaseAdapter, HttpCall, PublishRequest, ValidationIssue


class LinkedInAdapter(BaseAdapter):
    """LinkedIn Versioned Videos API: initialize -> upload parts -> finalize -> post."""

    platform = "linkedin"
    supports_native_schedule = False
    requires_public_media_url = False
    max_text_chars = 3000
    accepted_aspects = frozenset({"16:9", "1:1", "9:16"})
    api_version = "202506"

    def credential_names(self) -> list[str]:
        return ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_ORG_URN"]

    def build_calls(self, req: PublishRequest) -> list[HttpCall]:
        token = self._secret("LINKEDIN_ACCESS_TOKEN")
        owner = self._secret("LINKEDIN_ORG_URN")
        headers = {
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": self.api_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }

        part_size = 4 * 1024 * 1024
        parts = max(1, -(-req.media.bytes // part_size))

        calls = [
            HttpCall(
                seq=1, label="initialize_upload", method="POST",
                url="https://api.linkedin.com/rest/videos?action=initializeUpload",
                headers=headers,
                body={
                    "initializeUploadRequest": {
                        "owner": owner,
                        "fileSizeBytes": req.media.bytes,
                        "uploadCaptions": False,
                        "uploadThumbnail": False,
                    }
                },
                body_kind="json",
                note="Returns a video URN plus per-part upload instructions.",
            )
        ]

        for index in range(parts):
            offset = index * part_size
            length = min(part_size, req.media.bytes - offset)
            calls.append(
                HttpCall(
                    seq=len(calls) + 1,
                    label=f"upload_part_{index + 1}",
                    method="PUT",
                    url="{uploadInstructions[%d].uploadUrl}" % index,
                    headers={"Content-Type": "application/octet-stream"},
                    body={"$binary_ref": req.media.path, "offset": offset, "length": length},
                    body_kind="binary",
                    would_send_bytes=length,
                    note="Collect the ETag from each part; finalize needs them in order.",
                )
            )

        calls.append(
            HttpCall(
                seq=len(calls) + 1, label="finalize_upload", method="POST",
                url="https://api.linkedin.com/rest/videos?action=finalizeUpload",
                headers=headers,
                body={
                    "finalizeUploadRequest": {
                        "video": "{videoUrn}",
                        "uploadToken": "",
                        "uploadedPartIds": [f"{{etag_{i + 1}}}" for i in range(parts)],
                    }
                },
                body_kind="json",
            )
        )

        commentary = req.text
        if req.tracking_url:
            commentary = f"{commentary}\n\n{req.tracking_url}"
        if req.hashtags:
            commentary = f"{commentary}\n\n{' '.join(req.hashtags)}"

        calls.append(
            HttpCall(
                seq=len(calls) + 1, label="create_post", method="POST",
                url="https://api.linkedin.com/rest/posts",
                headers={**headers, "X-RestLi-Method": "CREATE"},
                body={
                    "author": owner,
                    "commentary": commentary,
                    "visibility": "PUBLIC",
                    "distribution": {
                        "feedDistribution": "MAIN_FEED",
                        "targetEntities": [],
                        "thirdPartyDistributionChannels": [],
                    },
                    "content": {"media": {"id": "{videoUrn}", "title": req.title or ""}},
                    "lifecycleState": "PUBLISHED",
                    "isReshareDisabledByAuthor": False,
                },
                body_kind="json",
            )
        )
        return calls


class InstagramAdapter(BaseAdapter):
    """Instagram Reels: create container -> poll status -> publish."""

    platform = "instagram"
    supports_native_schedule = False
    requires_public_media_url = True  # Graph API cannot take a local file
    max_text_chars = 2200
    max_duration_s = 90.0
    accepted_aspects = frozenset({"9:16"})
    graph_version = "v21.0"

    def credential_names(self) -> list[str]:
        return ["IG_USER_ID", "IG_ACCESS_TOKEN"]

    def build_calls(self, req: PublishRequest) -> list[HttpCall]:
        user = self._secret("IG_USER_ID")
        token = self._secret("IG_ACCESS_TOKEN")
        base = f"https://graph.facebook.com/{self.graph_version}"

        caption = req.text
        if req.hashtags:
            caption = f"{caption}\n\n{' '.join(req.hashtags)}"

        return [
            HttpCall(
                seq=1, label="check_publishing_limit", method="GET",
                url=f"{base}/{user}/content_publishing_limit",
                headers={"Authorization": f"Bearer {token}"},
                query={"fields": "config,quota_usage"},
                note="50 published posts per rolling 24h; check headroom before posting.",
            ),
            HttpCall(
                seq=2, label="create_container", method="POST",
                url=f"{base}/{user}/media",
                headers={"Authorization": f"Bearer {token}"},
                body={
                    "media_type": "REELS",
                    "video_url": req.media.public_url or "{MEDIA_PUBLIC_BASE_URL}/"
                    + f"{req.clip_id}-{req.variant}.mp4",
                    "caption": caption,
                    "share_to_feed": "true",
                },
                body_kind="form",
                would_send_bytes=req.media.bytes,
                note="Asynchronous: returns a creation_id that must be polled.",
            ),
            HttpCall(
                seq=3, label="poll_container_status", method="GET",
                url=f"{base}/{{creation_id}}",
                headers={"Authorization": f"Bearer {token}"},
                query={"fields": "status_code,status"},
                note="Poll every 5s until FINISHED, cap at 5 minutes.",
            ),
            HttpCall(
                seq=4, label="publish_container", method="POST",
                url=f"{base}/{user}/media_publish",
                headers={"Authorization": f"Bearer {token}"},
                body={"creation_id": "{creation_id}"},
                body_kind="form",
            ),
        ]


class YouTubeShortsAdapter(BaseAdapter):
    """YouTube resumable upload. The only adapter with native scheduling,
    and the only source of a real audience-retention curve."""

    platform = "youtube"
    supports_native_schedule = True
    max_text_chars = 5000
    max_duration_s = 180.0
    accepted_aspects = frozenset({"9:16"})

    def credential_names(self) -> list[str]:
        return ["YOUTUBE_ACCESS_TOKEN"]

    def validate(self, req: PublishRequest) -> list[ValidationIssue]:
        issues = super().validate(req)
        if req.title and len(req.title) > 100:
            issues.append(
                ValidationIssue("TITLE_TOO_LONG", "error", "YouTube titles cap at 100 chars")
            )
        issues.append(
            ValidationIssue(
                "QUOTA_COST", "warn",
                "Each upload costs 1600 quota units; the default 10,000/day "
                "allows only 6 uploads per day.",
            )
        )
        return issues

    def build_calls(self, req: PublishRequest) -> list[HttpCall]:
        token = self._secret("YOUTUBE_ACCESS_TOKEN")
        description = req.text
        if req.tracking_url:
            description = f"{description}\n\n{req.tracking_url}"

        status: dict = {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        }
        if req.scheduled_at_utc:
            status["publishAt"] = req.scheduled_at_utc

        return [
            HttpCall(
                seq=1, label="init_resumable_upload", method="POST",
                url="https://www.googleapis.com/upload/youtube/v3/videos",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(req.media.bytes),
                    "X-Upload-Content-Type": req.media.mime,
                },
                query={"uploadType": "resumable", "part": "snippet,status"},
                body={
                    "snippet": {
                        "title": (req.title or req.text[:90]),
                        "description": description,
                        "tags": [h.lstrip("#") for h in req.hashtags][:10],
                        "categoryId": "22",
                        "defaultLanguage": "tr",
                    },
                    "status": status,
                },
                body_kind="json",
                note="Response Location header carries the resumable session URI.",
            ),
            HttpCall(
                seq=2, label="upload_bytes", method="PUT",
                url="{resumable_session_uri}",
                headers={
                    "Content-Type": req.media.mime,
                    "Content-Length": str(req.media.bytes),
                },
                body={"$binary_ref": req.media.path},
                body_kind="binary",
                would_send_bytes=req.media.bytes,
                note="8 MB chunks; resume on HTTP 308.",
            ),
        ]


class XAdapter(BaseAdapter):
    """X chunked media upload then tweet."""

    platform = "x"
    supports_native_schedule = False
    max_text_chars = 280
    max_duration_s = 140.0

    def credential_names(self) -> list[str]:
        return ["X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]

    def validate(self, req: PublishRequest) -> list[ValidationIssue]:
        # A URL always counts as 23 characters regardless of real length.
        effective = len(req.text)
        if req.tracking_url:
            effective += 24
        issues = []
        if effective > self.max_text_chars:
            issues.append(
                ValidationIssue(
                    "TEXT_TOO_LONG", "error",
                    f"{effective} chars (URL counted as 23) exceeds 280",
                )
            )
        issues.extend(i for i in super().validate(req) if i.code != "TEXT_TOO_LONG")
        return issues

    def build_calls(self, req: PublishRequest) -> list[HttpCall]:
        auth = {"Authorization": 'OAuth oauth_consumer_key="<X_CONSUMER_KEY>", '
                                 'oauth_signature="<HMAC-SHA1>", oauth_version="1.0"'}
        chunk = 5 * 1024 * 1024
        chunks = max(1, -(-req.media.bytes // chunk))

        calls = [
            HttpCall(
                seq=1, label="media_init", method="POST",
                url="https://api.x.com/2/media/upload",
                headers=auth,
                body={
                    "command": "INIT",
                    "total_bytes": req.media.bytes,
                    "media_type": req.media.mime,
                    "media_category": "tweet_video",
                },
                body_kind="form",
                note="VERIFY: this account's tier may still use v1.1 media/upload.",
            )
        ]
        for index in range(chunks):
            calls.append(
                HttpCall(
                    seq=len(calls) + 1, label=f"media_append_{index}", method="POST",
                    url="https://api.x.com/2/media/upload",
                    headers=auth,
                    body={
                        "command": "APPEND",
                        "media_id": "{media_id}",
                        "segment_index": index,
                        "$binary_ref": req.media.path,
                    },
                    body_kind="multipart",
                    would_send_bytes=min(chunk, req.media.bytes - index * chunk),
                )
            )
        calls += [
            HttpCall(
                seq=len(calls) + 1, label="media_finalize", method="POST",
                url="https://api.x.com/2/media/upload",
                headers=auth,
                body={"command": "FINALIZE", "media_id": "{media_id}"},
                body_kind="form",
                note="Then poll STATUS until processing_info.state == succeeded.",
            ),
            HttpCall(
                seq=len(calls) + 2, label="create_tweet", method="POST",
                url="https://api.x.com/2/tweets",
                headers={**auth, "Content-Type": "application/json"},
                body={
                    "text": f"{req.text}\n{req.tracking_url}".strip(),
                    "media": {"media_ids": ["{media_id}"]},
                },
                body_kind="json",
            ),
        ]
        return calls


class TikTokAdapter(BaseAdapter):
    platform = "tiktok"
    supports_native_schedule = False
    max_text_chars = 2200
    accepted_aspects = frozenset({"9:16"})

    def credential_names(self) -> list[str]:
        return ["TIKTOK_ACCESS_TOKEN"]

    def validate(self, req: PublishRequest) -> list[ValidationIssue]:
        issues = super().validate(req)
        issues.append(
            ValidationIssue(
                "TIKTOK_UNAUDITED", "warn",
                "Unaudited apps are restricted to SELF_ONLY posting. This is an "
                "app-review gate, not a rate limit -- public posting will fail "
                "until the app passes audit.",
            )
        )
        return issues

    def build_calls(self, req: PublishRequest) -> list[HttpCall]:
        token = self._secret("TIKTOK_ACCESS_TOKEN")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        return [
            HttpCall(
                seq=1, label="query_creator_info", method="POST",
                url="https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
                headers=headers,
                note="Mandatory first call: returns the privacy levels this app may use.",
            ),
            HttpCall(
                seq=2, label="init_video_publish", method="POST",
                url="https://open.tiktokapis.com/v2/post/publish/video/init/",
                headers=headers,
                body={
                    "post_info": {
                        "title": req.text[:150],
                        "privacy_level": "SELF_ONLY",
                        "disable_comment": False,
                        "video_cover_timestamp_ms": 3500,
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": req.media.bytes,
                        "chunk_size": req.media.bytes,
                        "total_chunk_count": 1,
                    },
                },
                body_kind="json",
            ),
            HttpCall(
                seq=3, label="upload_video", method="PUT",
                url="{upload_url}",
                headers={
                    "Content-Type": req.media.mime,
                    "Content-Range": f"bytes 0-{max(0, req.media.bytes - 1)}/{req.media.bytes}",
                },
                body={"$binary_ref": req.media.path},
                body_kind="binary",
                would_send_bytes=req.media.bytes,
            ),
            HttpCall(
                seq=4, label="fetch_publish_status", method="POST",
                url="https://open.tiktokapis.com/v2/post/publish/status/fetch/",
                headers=headers,
                body={"publish_id": "{publish_id}"},
                body_kind="json",
            ),
        ]


REGISTRY: dict[str, type[BaseAdapter]] = {
    "linkedin": LinkedInAdapter,
    "instagram": InstagramAdapter,
    "youtube": YouTubeShortsAdapter,
    "x": XAdapter,
    "tiktok": TikTokAdapter,
}


def get_adapter(platform: str) -> BaseAdapter:
    try:
        return REGISTRY[platform]()
    except KeyError:
        raise ValueError(f"unknown platform {platform!r}; known: {sorted(REGISTRY)}") from None
