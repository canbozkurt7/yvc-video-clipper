"""Delivery stages: schedule, publish, collect, report, feedback.

These are grouped because they share the posts/metrics data model and
each is small on its own. All five are unit-level: a failure on one post
is recorded and the rest continue, because losing one platform's copy
should cost that platform's post, not the run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from yvc.io import read_json, write_json
from yvc.publish.adapters import get_adapter
from yvc.publish.base import DryRunAdapter, MediaAsset, PublishRequest
from yvc.report.analysis import MetricRow, analyze
from yvc.scheduling.planner import TR, plan


def run_delivery_stage(name: str, base: Path, config: dict) -> None:
    base = Path(base)
    if name == "schedule":
        _schedule(base, config)
    elif name == "publish":
        _publish(base, config)
    elif name == "collect":
        _collect(base, config)
    elif name == "report":
        _report(base, config)
    elif name == "feedback":
        _feedback(base, config)


def _schedule(base: Path, config: dict) -> None:
    posts = read_json(base / "posts.json")["posts"]
    now = datetime.now(TR)

    planned_by_platform: dict[str, list[datetime]] = {}
    siblings_by_clip: dict[str, list[datetime]] = {}
    rows = []

    for post in posts:
        if post.get("status") != "ok":
            continue
        platform = post["platform"]
        slot, rationale = plan(
            post["post_id"], platform, post.get("hook_type") or "curiosity_gap",
            now=now,
            already_planned=planned_by_platform.get(platform, []),
            clip_siblings=siblings_by_clip.get(post["clip_id"], []),
        )
        planned_by_platform.setdefault(platform, []).append(slot)
        siblings_by_clip.setdefault(post["clip_id"], []).append(slot)

        rows.append({
            "post_id": post["post_id"],
            "clip_id": post["clip_id"],
            "platform": platform,
            "scheduled_at_local": slot.isoformat(),
            "scheduled_at_utc": slot.astimezone(timezone.utc).isoformat(),
            "rationale": rationale.__dict__,
        })
        print(
            f"[schedule] {post['post_id']:28s} -> {rationale.weekday_choice} "
            f"{slot:%d %b %H:%M} ({rationale.rule_ids[0]})"
        )

    write_json(base / "schedule.json", {"scheduled": rows})
    print(f"[schedule] {len(rows)} posts scheduled")


def _posts_by_id(posts: list[dict]) -> dict:
    """Index posts.json by post_id, skipping rows that never got one.

    A clip whose copy failed is written out as {clip_id, status, issues}:
    there is no post to identify, so there is no post_id. Both publish
    and collect index by post_id, and both raised a bare KeyError on the
    first such row -- one failed clip taking down ten good posts, which
    is the opposite of the per-item degradation the stage intends.
    """
    return {p["post_id"]: p for p in posts if p.get("post_id")}


def _publish(base: Path, config: dict) -> None:
    raw_posts = read_json(base / "posts.json")["posts"]
    posts = _posts_by_id(raw_posts)
    scheduled = {s["post_id"]: s for s in read_json(base / "schedule.json")["scheduled"]}
    render = {r["clip_id"]: r for r in read_json(base / "render.json")["results"]}

    out_dir = base / "publish"
    results = []

    for post in raw_posts:
        if post.get("post_id"):
            continue
        # Counted, not dropped: publish.json accounts for every row in
        # posts.json or it cannot be read as a record of the run.
        results.append({
            "post_id": None,
            "clip_id": post.get("clip_id"),
            "platform": None,
            "status": "skipped_no_copy",
            "detail": "copywrite produced no text for this clip",
            "issues": post.get("issues", []),
        })
        print(
            f"[publish] {str(post.get('clip_id')):28s} skipped  "
            "no copy was written for this clip"
        )

    for post_id, post in posts.items():
        if post.get("status") != "ok":
            continue
        rendered = render.get(post["clip_id"])
        if not rendered or rendered.get("status") != "ok":
            results.append({
                "post_id": post_id, "platform": post["platform"],
                "status": "skipped_no_media",
                "detail": "clip did not render",
            })
            continue

        clip_path = Path(rendered["path"])
        aspect = rendered["aspect"]
        size = clip_path.stat().st_size if clip_path.exists() else 0

        adapter = get_adapter(post["platform"])
        missing = adapter.missing_credentials()

        media = MediaAsset(
            path=str(clip_path), mime="video/mp4", bytes=size,
            duration_s=rendered.get("duration_s") or 0.0,
            width=1080 if aspect == "9:16" else 1920,
            height=1920 if aspect == "9:16" else 1080,
            aspect=aspect,
            # In dry-run the host is not yet configured; emit the URL the
            # live path would use so the payload is complete.
            public_url=f"{{MEDIA_PUBLIC_BASE_URL}}/{clip_path.name}",
        )
        request = PublishRequest(
            post_id=post_id, clip_id=post["clip_id"], variant=post.get("variant", "A"),
            platform=post["platform"], text=post["text"], media=media,
            hashtags=post.get("hashtags", []), tracking_url=post.get("tracking_url", ""),
            title=post.get("title"),
            scheduled_at_utc=scheduled.get(post_id, {}).get("scheduled_at_utc"),
        )

        result = DryRunAdapter(adapter, out_dir).publish(request)
        errors = [i for i in result.issues if i.severity == "error"]
        # The adapter validates the request; it has no view on whether
        # the text is grounded in the clip. Carrying copywriting's own
        # verdict through means a post whose numbers failed the
        # hallucination gate cannot read as clean here.
        copy_errors = [
            i for i in post.get("validation", {}).get("issues", [])
            if i.get("severity") == "error"
        ]
        results.append({
            "post_id": post_id,
            "platform": post["platform"],
            "status": result.status,
            "mode": "dry_run",
            "copy_validation_errors": copy_errors,
            "missing_credentials": missing,
            "calls": len(result.calls),
            "issues": [i.__dict__ for i in result.issues],
            "proof_dir": str(out_dir / post["platform"] / post_id),
            # Empty in dry-run. A live adapter fills it, and so does
            # remote_ids.json for clips uploaded outside the pipeline --
            # either way this is what the collector joins real metrics on.
            "remote_id": result.remote_id,
            "permalink": result.permalink,
            "published_at_utc": scheduled.get(post_id, {}).get("scheduled_at_utc"),
        })
        print(
            f"[publish] {post_id:28s} {result.status:8s} "
            f"{len(result.calls)} calls, {len(errors)} errors"
            + (f", {len(copy_errors)} copy errors" if copy_errors else "")
            + (f", missing creds: {missing}" if missing else "")
        )

    write_json(base / "publish.json", {"mode": "dry_run", "results": results})
    _write_proof_readme(out_dir, results)
    print(f"[publish] {len(results)} posts -> {out_dir}")


def _write_proof_readme(out_dir: Path, results: list[dict]) -> None:
    """Human-readable index of what would have been sent."""
    lines = [
        "# Publish proof (dry run)",
        "",
        "These files are produced by the same `build_calls()` the live path",
        "uses, so they are the exact requests that would be sent -- not mocks.",
        "Adding credentials to `.env` switches the adapter to live with no",
        "code change.",
        "",
        "| post | platform | status | calls | missing credentials |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        creds = ", ".join(r.get("missing_credentials") or []) or "-"
        lines.append(
            f"| {r.get('post_id') or r.get('clip_id') or '-'} "
            f"| {r.get('platform') or '-'} | {r['status']} | "
            f"{r.get('calls', 0)} | {creds} |"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "PUBLISH_PROOF.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )


def _remote_id_overrides(base: Path) -> dict:
    """Real platform ids for clips published outside the pipeline.

    The pipeline defaults to dry-run, so nothing has a remote id until
    credentials exist. But a clip uploaded by hand still has real
    analytics behind it, and refusing to read them until the whole live
    publish path is wired would throw away the only real data available.

    ``remote_ids.json`` maps post_id (or clip_id, which covers every post
    of that clip) to the platform id and its publish time:

        {"c03": {"platform": "youtube",
                 "remote_id": "dQw4w9WgXcQ",
                 "published_at_utc": "2026-08-20T09:00:00Z"}}
    """
    path = base / "remote_ids.json"
    if not path.exists():
        return {}
    data = read_json(path)
    return data.get("ids", data)


def _collect(base: Path, config: dict) -> None:
    """Gather metrics: real where an API returned them, simulated otherwise."""
    from yvc.metrics.collectors import collector_status, get_collector
    from yvc.metrics.simulator import SCHEMA_FIELDS, SimContext, row_provenance, simulate

    posts = _posts_by_id(read_json(base / "posts.json")["posts"])
    published = read_json(base / "publish.json")["results"]
    render = {r["clip_id"]: r for r in read_json(base / "render.json")["results"]}
    overrides = _remote_id_overrides(base)

    windows = config.get("metrics", {}).get("windows", ["T+24h"])
    rows = []

    # Report collector availability once, up front. A silent absence of
    # real data is indistinguishable from a broken collector.
    for platform in sorted({p["platform"] for p in posts.values()}):
        usable, reason = collector_status(platform)
        print(f"[collect] {platform:10s} collector: "
              f"{'READY' if usable else 'unavailable'} ({reason})")
    if overrides:
        print(f"[collect] {len(overrides)} remote id override(s) from remote_ids.json")

    for entry in published:
        post = posts.get(entry["post_id"])
        if not post:
            continue
        rendered = render.get(post["clip_id"], {})

        platform = post["platform"]
        override = overrides.get(entry["post_id"]) or overrides.get(post["clip_id"]) or {}
        if override and override.get("platform") not in (None, platform):
            override = {}
        remote_id = override.get("remote_id") or entry.get("remote_id") or ""
        published_at = (
            override.get("published_at_utc")
            or entry.get("published_at_utc")
            or ""
        )

        for window in windows:
            real: dict = {}
            notes: list[str] = []
            errors: list[str] = []

            collector = get_collector(platform)
            if collector is not None:
                fetched = collector.fetch(
                    remote_id=remote_id, window=window,
                    published_at_utc=published_at,
                    duration_s=rendered.get("duration_s") or 30.0,
                )
                real = fetched.values
                notes, errors = fetched.notes, fetched.errors
                for message in errors:
                    print(f"[collect] WARNING {platform} {window}: {message}")

            missing = set(SCHEMA_FIELDS) - set(real)
            sim = simulate(
                SimContext(
                    post_id=entry["post_id"], platform=post["platform"],
                    hook_type=post.get("hook_type") or "curiosity_gap",
                    duration_s=rendered.get("duration_s") or 30.0,
                    window=window,
                ),
                missing, real=real,
            )
            rows.append({
                "post_id": entry["post_id"],
                "clip_id": post["clip_id"],
                "platform": post["platform"],
                "hook_type": post.get("hook_type"),
                "variant": post.get("variant", "A"),
                "window": window,
                **sim.values,
                "provenance": row_provenance(sim.provenance),
                "provenance_detail": sim.provenance,
                "remote_id": remote_id or None,
                "collector_notes": notes or None,
                "collector_errors": errors or None,
            })

    write_json(base / "metrics.json", {"rows": rows})
    simulated = sum(1 for r in rows if r["provenance"] == "SIMULATED")
    mixed = sum(1 for r in rows if r["provenance"] == "MIXED")
    real_rows = len(rows) - simulated - mixed
    print(
        f"[collect] {len(rows)} metric rows: {real_rows} REAL, "
        f"{mixed} MIXED, {simulated} SIMULATED"
    )


def _report(base: Path, config: dict) -> None:
    from yvc.report.render_html import render_report

    rows_raw = read_json(base / "metrics.json")["rows"]
    # Analyse the most mature window only; mixing windows would compare
    # posts at different stages of their life.
    windows = [r["window"] for r in rows_raw]
    target = "T+24h" if "T+24h" in windows else (windows[-1] if windows else None)
    rows = [
        MetricRow(
            post_id=r["post_id"], clip_id=r["clip_id"], platform=r["platform"],
            hook_type=r.get("hook_type") or "unknown", variant=r.get("variant", "A"),
            impressions=r.get("impressions", 0), views_3s=r.get("views_3s", 0),
            completion_rate=r.get("completion_rate", 0.0),
            engagement_rate=r.get("engagement_rate", 0.0),
            ctr=r.get("ctr", 0.0),
            hook_retention_3s=r.get("hook_retention_3s", 0.0),
            provenance_detail=r.get("provenance_detail", {}),
        )
        for r in rows_raw if r["window"] == target
    ]

    verdict = analyze(rows)
    print(f"[report] verdict: {verdict.sentence_tr}")

    out = render_report(base, rows_raw, verdict, config)
    write_json(base / "report" / "report.json", {
        "verdict": verdict.__dict__,
        "window_analyzed": target,
        "row_count": len(rows_raw),
    })
    print(f"[report] -> {out}")


def _feedback(base: Path, config: dict) -> None:
    from yvc.feedback.priors import Outcome, compute_priors
    from yvc.report.analysis import HQS_WEIGHTS

    rows = read_json(base / "metrics.json")["rows"]
    target = [r for r in rows if r["window"] == "T+24h"] or rows

    # HQS must be computed from platform-normalised z-scores, exactly as
    # the report does. Using raw values instead would make an Instagram
    # post with 12k impressions incomparable to a LinkedIn post with 3.8k,
    # and every hook type would land on a near-identical multiplier --
    # the loop would look like it worked while learning nothing.
    from yvc.report.analysis import MetricRow, _zscore_within_platform

    metric_rows = [
        MetricRow(
            post_id=r["post_id"], clip_id=r["clip_id"], platform=r["platform"],
            hook_type=r.get("hook_type") or "unknown",
            completion_rate=r.get("completion_rate", 0.0),
            engagement_rate=r.get("engagement_rate", 0.0),
            ctr=r.get("ctr", 0.0),
            hook_retention_3s=r.get("hook_retention_3s", 0.0),
            provenance_detail=r.get("provenance_detail", {}),
        )
        for r in target
    ]
    z = {
        field_name: _zscore_within_platform(metric_rows, field_name)
        for field_name in HQS_WEIGHTS
    }
    # Provenance is measured per field, not asserted per row. A real
    # YouTube row is always MIXED -- the API returns no impression count --
    # so a row-level label would reject every genuine observation the
    # pipeline can collect. See feedback.priors.real_hqs_weight.
    from yvc.feedback.priors import real_hqs_weight

    outcomes = [
        Outcome(
            hook_type=row.hook_type,
            hqs=sum(w * z[k][row.post_id] for k, w in HQS_WEIGHTS.items()),
            age_days=0.0,
            real_hqs_weight=real_hqs_weight(row.provenance_detail, HQS_WEIGHTS),
        )
        for row in metric_rows
    ]

    # Pull in every previous video's outcomes so priors accumulate across
    # runs. Without this read the loop would recompute from scratch each
    # time and never actually learn anything.
    from yvc.db.store import load_outcomes

    historical = load_outcomes()
    if historical:
        print(f"[feedback] {len(historical)} historical outcomes loaded from hook DB")
    combined = historical + outcomes

    # Only measured outcomes move the multipliers. Simulated rows still
    # reach the report, where they are labelled -- but learning from the
    # simulator would mean learning back its own hook-type assumptions,
    # which is circular and would look exactly like a working loop.
    from yvc.feedback.priors import MIN_REAL_HQS_WEIGHT, teaching_outcomes

    teaching = teaching_outcomes(combined)
    excluded = len(combined) - len(teaching)
    print(
        f"[feedback] {len(teaching)}/{len(combined)} outcomes teach "
        f"(need >= {MIN_REAL_HQS_WEIGHT:.2f} of HQS weight measured)"
    )

    hook_types = sorted({o.hook_type for o in combined})
    priors = compute_priors(teaching, hook_types, seed=base.name)

    if teaching:
        warning = (
            f"{len(teaching)} measured outcomes drive these multipliers; "
            f"{excluded} simulated or partially measured outcomes were excluded."
        )
    else:
        warning = (
            "No outcome carries enough measured signal yet, so every "
            "multiplier is exactly 1.0 and scoring is unaffected. Publish "
            "clips and collect real metrics to give the loop something to "
            "learn from."
        )

    payload = {
        "params": priors.params,
        "priors": priors.as_rows(),
        "teaching_outcomes": len(teaching),
        "excluded_outcomes": excluded,
        "min_real_hqs_weight": MIN_REAL_HQS_WEIGHT,
        "note": "Multipliers feed the next video's hook scoring: "
                "S(c) = M(hook) * sum(w_j * s_j). Bounded to [0.80, 1.25] so "
                "a learned preference tilts ranking without dictating it.",
        "provenance_warning": warning,
    }
    write_json(base / "feedback.json", payload)

    # Persist the whole run last, so this video's clips, posts and metrics
    # become history for the next one.
    try:
        from yvc.db.store import record_run

        counts = record_run(base)
        print(f"[feedback] persisted to hook DB: {counts}")
    except Exception as exc:
        # A storage failure must not invalidate a finished run.
        print(f"[feedback] WARNING could not persist to hook DB: {exc}")

    for row in priors.as_rows():
        print(
            f"[feedback] {row['hook_type']:14s} n_eff={row['n_eff']:5.2f} "
            f"M={row['multiplier']:.3f}"
        )
