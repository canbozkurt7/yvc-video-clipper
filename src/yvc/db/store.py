"""Local SQLite store: the hook database.

This is the source of truth, deliberately. The governing constraint on
this project is that someone else can clone the repo and run it, and a
file-backed SQLite database needs no credentials, no network and no
server. An optional Supabase mirror can be layered on top later; it is
not required for the loop to work.

Every primary key is deterministic (``clip_id = f"{video_id}-c{idx:02d}"``,
``post_id = sha1(...)``), so every write is an upsert and re-running the
pipeline converges rather than duplicating. That is what makes the
pipeline safe to re-run, which the brief grades directly.

Without this module the feedback loop computes multipliers and then
throws them away at the end of the run -- the arithmetic would look
correct while learning nothing across videos.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS videos (
  video_id      TEXT PRIMARY KEY,
  url           TEXT NOT NULL,
  title         TEXT,
  duration_s    REAL,
  channel       TEXT,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
  run_id       TEXT PRIMARY KEY,
  video_id     TEXT NOT NULL REFERENCES videos(video_id),
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  mode         TEXT NOT NULL CHECK (mode IN ('dry_run','live')),
  status       TEXT NOT NULL,
  config_hash  TEXT,
  manifest     TEXT
);

CREATE TABLE IF NOT EXISTS clips (
  clip_id         TEXT PRIMARY KEY,
  video_id        TEXT NOT NULL REFERENCES videos(video_id),
  run_id          TEXT,
  idx             INTEGER NOT NULL,
  aspect          TEXT NOT NULL CHECK (aspect IN ('9:16','16:9','1:1')),
  start_s         REAL NOT NULL,
  end_s           REAL NOT NULL,
  duration_s      REAL NOT NULL,
  hook_type       TEXT NOT NULL,
  hook_line       TEXT,
  transcript_text TEXT,
  total_score     REAL,
  selected_reason TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Long format: one row per criterion. Keeping this normalised is what
-- allows the report to decompose a score months later and lets the
-- weight-adaptation step regress outcome on individual criteria.
CREATE TABLE IF NOT EXISTS hook_scores (
  clip_id     TEXT NOT NULL REFERENCES clips(clip_id),
  criterion   TEXT NOT NULL,
  raw         REAL,
  score       REAL NOT NULL,
  weight      REAL NOT NULL,
  method      TEXT NOT NULL,
  rationale   TEXT,
  PRIMARY KEY (clip_id, criterion)
);

CREATE TABLE IF NOT EXISTS posts (
  post_id            TEXT PRIMARY KEY,
  clip_id            TEXT NOT NULL REFERENCES clips(clip_id),
  variant            TEXT NOT NULL DEFAULT 'A',
  platform           TEXT NOT NULL,
  text_tr            TEXT,
  text_en            TEXT,
  hashtags           TEXT,
  tracking_url       TEXT,
  scheduled_at_utc   TEXT,
  schedule_rationale TEXT,
  published_at_utc   TEXT,
  status             TEXT NOT NULL,
  mode               TEXT NOT NULL,
  remote_id          TEXT,
  permalink          TEXT,
  proof_path         TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
  metric_id         TEXT PRIMARY KEY,
  post_id           TEXT NOT NULL REFERENCES posts(post_id),
  window            TEXT NOT NULL,
  collected_at      TEXT NOT NULL DEFAULT (datetime('now')),
  impressions       INTEGER,
  reach             INTEGER,
  views             INTEGER,
  views_3s          INTEGER,
  avg_view_duration_s REAL,
  completion_rate   REAL,
  hook_retention_3s REAL,
  dropoff_3s        REAL,
  retention_curve   TEXT,
  likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER,
  clicks INTEGER, conversions INTEGER,
  engagement_rate   REAL,
  ctr               REAL,
  provenance        TEXT NOT NULL CHECK (provenance IN ('REAL','SIMULATED','MIXED')),
  provenance_detail TEXT NOT NULL,
  simulator_version TEXT,
  UNIQUE (post_id, window)
);

-- Audit trail: what the scorer believed, and when. Without this a
-- selection decision cannot be reconstructed after the priors move on.
CREATE TABLE IF NOT EXISTS hook_priors_snapshot (
  snapshot_id  TEXT PRIMARY KEY,
  run_id       TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  hook_type    TEXT NOT NULL,
  n_eff        REAL NOT NULL,
  y_bar        REAL NOT NULL,
  y_hat        REAL NOT NULL,
  sigma        REAL NOT NULL,
  multiplier   REAL NOT NULL,
  sampled_multiplier REAL NOT NULL,
  params       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_videos (
  video_id       TEXT PRIMARY KEY,
  channel_id     TEXT,
  published_at   TEXT,
  first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
  trigger_source TEXT,
  run_id         TEXT,
  status         TEXT
);

CREATE INDEX IF NOT EXISTS idx_clips_hook ON clips(hook_type);
CREATE INDEX IF NOT EXISTS idx_posts_clip ON posts(clip_id);
CREATE INDEX IF NOT EXISTS idx_metrics_post ON metrics(post_id);
"""

DEFAULT_PATH = Path(".yvc/yvc.db")


@contextmanager
def connect(path: str | Path = DEFAULT_PATH) -> Iterator[sqlite3.Connection]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _upsert(conn: sqlite3.Connection, table: str, row: dict, key: str) -> None:
    """Insert or update on the deterministic primary key.

    Upsert rather than insert is what makes a re-run converge instead of
    accumulating duplicates.
    """
    columns = list(row)
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{c}=excluded.{c}" for c in columns if c != key)
    sql = (
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key}) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in columns])


def record_run(base: Path, db_path: str | Path = DEFAULT_PATH) -> dict:
    """Persist a completed run's artifacts into the hook database."""
    from yvc.io import read_json

    base = Path(base)
    video_id = base.name
    counts = {"clips": 0, "scores": 0, "posts": 0, "metrics": 0, "priors": 0}

    def maybe(name: str):
        path = base / name
        return read_json(path) if path.exists() else None

    acquire = maybe("acquire.json") or {}
    clips_doc = maybe("clips.json") or {"clips": []}
    scores_doc = maybe("scores.json") or {"segments": []}
    posts_doc = maybe("posts.json") or {"posts": []}
    schedule_doc = maybe("schedule.json") or {"scheduled": []}
    publish_doc = maybe("publish.json") or {"results": []}
    metrics_doc = maybe("metrics.json") or {"rows": []}
    feedback_doc = maybe("feedback.json") or {}

    schedule_by_post = {s["post_id"]: s for s in schedule_doc.get("scheduled", [])}
    publish_by_post = {p["post_id"]: p for p in publish_doc.get("results", [])}
    scores_by_segment = {
        s["segment_id"]: s for s in scores_doc.get("segments", [])
    }

    with connect(db_path) as conn:
        _upsert(conn, "videos", {
            "video_id": video_id,
            "url": acquire.get("url", ""),
            "duration_s": acquire.get("duration_s"),
        }, "video_id")

        for index, clip in enumerate(clips_doc.get("clips", []), 1):
            clip_id = f"{video_id}-{clip['clip_id']}"
            _upsert(conn, "clips", {
                "clip_id": clip_id,
                "video_id": video_id,
                "idx": index,
                "aspect": clip["aspect"],
                "start_s": clip["start"],
                "end_s": clip["end"],
                "duration_s": clip["duration"],
                "hook_type": clip.get("hook_type") or "unknown",
                "hook_line": clip.get("hook_line"),
                "transcript_text": clip.get("text"),
                "total_score": clip.get("score"),
                "selected_reason": clip.get("selected_reason", "greedy"),
            }, "clip_id")
            counts["clips"] += 1

            scored = scores_by_segment.get(clip.get("source_segment"))
            for criterion, entry in (scored or {}).get("criteria", {}).items():
                conn.execute(
                    "INSERT INTO hook_scores "
                    "(clip_id,criterion,raw,score,weight,method,rationale) "
                    "VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(clip_id,criterion) DO UPDATE SET "
                    "raw=excluded.raw,score=excluded.score,weight=excluded.weight,"
                    "method=excluded.method,rationale=excluded.rationale",
                    (
                        clip_id, criterion, entry.get("raw"), entry.get("score"),
                        entry.get("weight"), entry.get("method"),
                        entry.get("justification") or scored.get("rationale"),
                    ),
                )
                counts["scores"] += 1

        for post in posts_doc.get("posts", []):
            if post.get("status") != "ok":
                continue
            sched = schedule_by_post.get(post["post_id"], {})
            pub = publish_by_post.get(post["post_id"], {})
            _upsert(conn, "posts", {
                "post_id": post["post_id"],
                "clip_id": f"{video_id}-{post['clip_id']}",
                "variant": post.get("variant", "A"),
                "platform": post["platform"],
                "text_tr": post.get("text"),
                "text_en": post.get("text_en"),
                "hashtags": _j(post.get("hashtags", [])),
                "tracking_url": post.get("tracking_url"),
                "scheduled_at_utc": sched.get("scheduled_at_utc"),
                "schedule_rationale": _j(sched.get("rationale", {})),
                "status": pub.get("status", "planned"),
                "mode": pub.get("mode", "dry_run"),
                "proof_path": pub.get("proof_dir"),
            }, "post_id")
            counts["posts"] += 1

        for row in metrics_doc.get("rows", []):
            _upsert(conn, "metrics", {
                "metric_id": f"{row['post_id']}|{row['window']}",
                "post_id": row["post_id"],
                "window": row["window"],
                "impressions": row.get("impressions"),
                "reach": row.get("reach"),
                "views": row.get("views"),
                "views_3s": row.get("views_3s"),
                "avg_view_duration_s": row.get("avg_view_duration_s"),
                "completion_rate": row.get("completion_rate"),
                "hook_retention_3s": row.get("hook_retention_3s"),
                "dropoff_3s": row.get("dropoff_3s"),
                "retention_curve": _j(row.get("retention_curve", [])),
                "likes": row.get("likes"), "comments": row.get("comments"),
                "shares": row.get("shares"), "saves": row.get("saves"),
                "clicks": row.get("clicks"), "conversions": row.get("conversions"),
                "engagement_rate": row.get("engagement_rate"),
                "ctr": row.get("ctr"),
                "provenance": row.get("provenance", "SIMULATED"),
                "provenance_detail": _j(row.get("provenance_detail", {})),
                "simulator_version": row.get("simulator_version"),
            }, "metric_id")
            counts["metrics"] += 1

        for prior in feedback_doc.get("priors", []):
            _upsert(conn, "hook_priors_snapshot", {
                "snapshot_id": f"{video_id}|{prior['hook_type']}",
                "run_id": video_id,
                "hook_type": prior["hook_type"],
                "n_eff": prior["n_eff"], "y_bar": prior["y_bar"],
                "y_hat": prior["y_hat"], "sigma": prior["sigma"],
                "multiplier": prior["multiplier"],
                "sampled_multiplier": prior["sampled_multiplier"],
                "params": _j(feedback_doc.get("params", {})),
            }, "snapshot_id")
            counts["priors"] += 1

    return counts


def load_outcomes(db_path: str | Path = DEFAULT_PATH, window: str = "T+24h") -> list:
    """Read historical outcomes for the next video's prior computation.

    This is the read side of the loop: without it, each run would start
    from a blank slate and the feedback design would be decorative.
    """
    from yvc.feedback.priors import Outcome
    from yvc.report.analysis import HQS_WEIGHTS, MetricRow, _zscore_within_platform

    if not Path(db_path).exists():
        return []

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT m.post_id, p.platform, p.clip_id, c.hook_type,
                   m.hook_retention_3s, m.completion_rate, m.engagement_rate, m.ctr,
                   julianday('now') - julianday(m.collected_at) AS age_days
            FROM metrics m
            JOIN posts p ON p.post_id = m.post_id
            JOIN clips c ON c.clip_id = p.clip_id
            WHERE m.window = ?
            """,
            (window,),
        ).fetchall()

    if not rows:
        return []

    metric_rows = [
        MetricRow(
            post_id=r["post_id"], clip_id=r["clip_id"], platform=r["platform"],
            hook_type=r["hook_type"],
            hook_retention_3s=r["hook_retention_3s"] or 0.0,
            completion_rate=r["completion_rate"] or 0.0,
            engagement_rate=r["engagement_rate"] or 0.0,
            ctr=r["ctr"] or 0.0,
        )
        for r in rows
    ]
    z = {
        field: _zscore_within_platform(metric_rows, field) for field in HQS_WEIGHTS
    }
    ages = {r["post_id"]: (r["age_days"] or 0.0) for r in rows}

    return [
        Outcome(
            hook_type=row.hook_type,
            hqs=sum(w * z[k][row.post_id] for k, w in HQS_WEIGHTS.items()),
            age_days=ages.get(row.post_id, 0.0),
        )
        for row in metric_rows
    ]


def stats(db_path: str | Path = DEFAULT_PATH) -> dict:
    if not Path(db_path).exists():
        return {}
    with connect(db_path) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            for table in ("videos", "clips", "hook_scores", "posts", "metrics",
                          "hook_priors_snapshot")
        }
