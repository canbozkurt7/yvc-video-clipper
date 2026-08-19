"""YouTube metrics collector -- the pipeline's one source of real retention.

Three calls, each earning its place:

* **Data API ``videos.list``** -- lifetime counters (likes, comments).
  Cheap, and independent of the analytics pipeline's processing delay.
* **Analytics API ``reports.query``** -- windowed core metrics. This is
  where average view duration and average view percentage come from, and
  it respects the T+24h / T+7d window the report compares on.
* **Analytics API with ``elapsedVideoTimeRatio``** -- the audience
  retention curve. No other platform in this pipeline exposes one, which
  makes it the calibration anchor for every simulated row: 3-second
  retention stops being an assumption and becomes a measurement.

Quota note: ``videos.list`` costs 1 unit and Analytics queries are free,
so collecting five clips across four windows is negligible against the
10 000/day budget. Uploading is the expensive verb (1600 units), not
measuring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from yvc.metrics.collectors.base import WINDOW_DAYS, CollectorResult, env, tls_verify

TOKEN_URL = "https://oauth2.googleapis.com/token"
DATA_API = "https://www.googleapis.com/youtube/v3/videos"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"

CORE_METRICS = ",".join([
    "views", "estimatedMinutesWatched", "averageViewDuration",
    "averageViewPercentage", "likes", "comments", "shares",
    "subscribersGained", "annotationClickThroughRate",
])


class YouTubeCollector:
    """Reads real analytics for videos owned by the authenticated channel."""

    platform = "youtube"

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires = datetime(1970, 1, 1, tzinfo=timezone.utc)

    # Credentials are read on access, not at construction. The registry
    # caches collector instances, so reading them once in __init__ would
    # mean credentials exported after the first status check are ignored
    # for the rest of the process -- a confusing way to lose real data.
    @property
    def client_id(self) -> str | None:
        return env("YT_CLIENT_ID", "YOUTUBE_CLIENT_ID", "GOOGLE_CLIENT_ID")

    @property
    def client_secret(self) -> str | None:
        return env("YT_CLIENT_SECRET", "YOUTUBE_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET")

    @property
    def refresh_token(self) -> str | None:
        return env("YT_REFRESH_TOKEN", "YOUTUBE_REFRESH_TOKEN", "GOOGLE_REFRESH_TOKEN")

    # --- credentials -------------------------------------------------

    def credentials_status(self) -> tuple[bool, str]:
        missing = [
            name
            for name, value in (
                ("YT_CLIENT_ID", self.client_id),
                ("YT_CLIENT_SECRET", self.client_secret),
                ("YT_REFRESH_TOKEN", self.refresh_token),
            )
            if not value
        ]
        if missing:
            return False, f"missing {', '.join(missing)}"
        return True, "refresh token present"

    def _client(self):
        import httpx

        return httpx.Client(timeout=30.0, verify=tls_verify())

    def access_token(self) -> str:
        """Exchange the refresh token, caching until shortly before expiry."""
        now = datetime.now(timezone.utc)
        if self._token and now < self._token_expires:
            return self._token

        with self._client() as client:
            response = client.post(
                TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        # Renew a minute early; a token expiring mid-collection would fail
        # one window and look like a data problem rather than an auth one.
        self._token_expires = now + timedelta(
            seconds=int(payload.get("expires_in", 3600)) - 60
        )
        return self._token

    # --- fetching ----------------------------------------------------

    def fetch(
        self, *, remote_id: str, window: str, published_at_utc: str, duration_s: float
    ) -> CollectorResult:
        result = CollectorResult(source="youtube-data-v3+analytics-v2")
        usable, reason = self.credentials_status()
        if not usable:
            result.notes.append(f"youtube collector unavailable: {reason}")
            return result
        if not remote_id:
            result.notes.append("no remote video id (not published live yet)")
            return result

        days = WINDOW_DAYS.get(window)
        if days is None:
            result.notes.append(f"unknown window {window}")
            return result
        if days == 0:
            # Explicit beats quietly reporting a day of data as an hour.
            result.notes.append(
                f"{window} is finer than the daily granularity of YouTube "
                "Analytics; simulated instead of approximated"
            )
            return result

        start = _parse_date(published_at_utc)
        if start is None:
            result.notes.append("unparseable publish date; cannot bound the window")
            return result
        end = min(start + timedelta(days=days), datetime.now(timezone.utc))
        if end < start:
            result.notes.append("publish date is in the future; nothing to collect")
            return result

        headers = {"Authorization": f"Bearer {self.access_token()}"}
        values: dict = {}

        with self._client() as client:
            _merge(result, values, "analytics core",
                   lambda: self._core(client, headers, remote_id, start, end))
            _merge(result, values, "retention curve",
                   lambda: self._retention(client, headers, remote_id, start, end,
                                           duration_s))
            _merge(result, values, "data api counters",
                   lambda: self._lifetime(client, headers, remote_id))

        result.values = values
        result.fetched_at = datetime.now(timezone.utc).isoformat()
        return result

    # --- individual calls --------------------------------------------

    def _core(self, client, headers, video_id, start, end) -> dict:
        response = client.get(
            ANALYTICS_API,
            headers=headers,
            params={
                "ids": "channel==MINE",
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": end.strftime("%Y-%m-%d"),
                "metrics": CORE_METRICS,
                "filters": f"video=={video_id}",
            },
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("rows") or []
        if not rows:
            return {}
        names = [c["name"] for c in payload.get("columnHeaders", [])]
        row = dict(zip(names, rows[0]))

        out: dict = {}
        if "views" in row:
            out["views"] = int(row["views"])
            # This endpoint reports no impression count. Reach is left absent
            # rather than aliased to views, which would inflate every rate
            # derived from it.
        if "averageViewDuration" in row:
            out["avg_view_duration_s"] = float(row["averageViewDuration"])
        if "averageViewPercentage" in row:
            out["completion_rate"] = round(
                float(row["averageViewPercentage"]) / 100.0, 4
            )
        for source_name, field_name in (
            ("likes", "likes"), ("comments", "comments"), ("shares", "shares")
        ):
            if source_name in row:
                out[field_name] = int(row[source_name])
        if "annotationClickThroughRate" in row:
            out["ctr"] = round(float(row["annotationClickThroughRate"]), 5)
        return out

    def _retention(self, client, headers, video_id, start, end, duration_s) -> dict:
        """Audience retention -- the measurement the rubric is judged against."""
        response = client.get(
            ANALYTICS_API,
            headers=headers,
            params={
                "ids": "channel==MINE",
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": end.strftime("%Y-%m-%d"),
                "metrics": "audienceWatchRatio",
                "dimensions": "elapsedVideoTimeRatio",
                "filters": f"video=={video_id}",
                "sort": "elapsedVideoTimeRatio",
            },
        )
        response.raise_for_status()
        rows = response.json().get("rows") or []
        if not rows or duration_s <= 0:
            return {}

        curve = [
            [round(float(r[0]) * duration_s, 2), round(float(r[1]), 4)] for r in rows
        ]
        out: dict = {"retention_curve": curve}

        retention = _interpolate(rows, 3.0 / duration_s)
        if retention is not None:
            out["hook_retention_3s"] = round(retention, 4)
            out["dropoff_3s"] = round(max(0.0, 1.0 - retention), 4)
        return out

    def _lifetime(self, client, headers, video_id) -> dict:
        response = client.get(
            DATA_API, headers=headers,
            params={"part": "statistics", "id": video_id},
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        if not items:
            return {}
        stats = items[0].get("statistics", {})
        out: dict = {}
        # setdefault in the caller means a windowed value already collected
        # is never overwritten by a lifetime counter.
        if "likeCount" in stats:
            out["likes"] = int(stats["likeCount"])
        if "commentCount" in stats:
            out["comments"] = int(stats["commentCount"])
        return out


def _merge(result: CollectorResult, values: dict, label: str, call) -> None:
    """Run one API call; a failure costs that call's fields, not the row."""
    try:
        for key, value in (call() or {}).items():
            values.setdefault(key, value)
    except Exception as exc:
        result.errors.append(f"{label}: {type(exc).__name__}: {exc}")


def _interpolate(rows: list, target_ratio: float) -> float | None:
    """Watch ratio at a fractional position, linearly between samples."""
    points = sorted((float(r[0]), float(r[1])) for r in rows)
    if not points:
        return None
    if target_ratio <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= target_ratio <= x1:
            if x1 == x0:
                return y1
            return y0 + (target_ratio - x0) / (x1 - x0) * (y1 - y0)
    return points[-1][1]


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
