"""One clip without copy must not take the publish stage down with it.

Copywriting degrades per clip: a clip whose LLM call fails is written to
posts.json as {clip_id, status, issues} and never gets a post_id, because
there is no post to identify. Publish indexed posts by post_id directly
and raised a bare KeyError on the first such row -- with ten good posts
sitting behind it, and nothing in the message saying which clip or why.
"""

from __future__ import annotations

from yvc.io import read_json, write_json
from yvc.stages.s10_deliver import run_delivery_stage


def _fixture(base):
    clip_dir = base / "clips" / "c01"
    clip_dir.mkdir(parents=True)
    (clip_dir / "clip.mp4").write_bytes(b"not really an mp4")

    write_json(base / "posts.json", {"posts": [
        {
            "post_id": "c01-instagram-A", "clip_id": "c01", "variant": "A",
            "platform": "instagram", "status": "ok", "text": "kanca",
            "hashtags": ["#test"], "tracking_url": "https://example.com",
            "title": None,
            "validation": {"passed": False, "issues": [
                {"code": "NUMBER_HALLUCINATION", "severity": "error",
                 "detail": "key_number '18 milyon' is not in the clip"},
            ]},
        },
        # The shape copywrite writes when the model never answered.
        {
            "clip_id": "c03", "status": "failed",
            "issues": [{"code": "LLM_FAILED", "severity": "error",
                        "detail": "copy.c03.a1: failed after 3 attempts"}],
        },
    ]})
    write_json(base / "schedule.json", {"scheduled": [
        {"post_id": "c01-instagram-A", "clip_id": "c01", "platform": "instagram",
         "scheduled_at_local": "2026-08-28 19:50", "scheduled_at_utc":
         "2026-08-28T16:50:00Z", "rationale": {}},
    ]})
    write_json(base / "render.json", {"encoder": "libx264", "ok": 1, "failed": 0,
        "results": [{"clip_id": "c01", "aspect": "9:16", "status": "ok",
                     "path": str(clip_dir / "clip.mp4"), "duration_s": 30.0}]})


def test_a_clip_with_no_copy_is_reported_not_raised(tmp_path):
    base = tmp_path / "vid"
    base.mkdir()
    _fixture(base)

    run_delivery_stage("publish", base, {})

    results = read_json(base / "publish.json")["results"]
    by_status = {r["status"] for r in results}
    assert "skipped_no_copy" in by_status
    assert len(results) == 2, (
        "publish.json accounts for every row in posts.json, or it cannot "
        "be read as a record of what the run did"
    )


def test_the_good_post_is_still_published(tmp_path):
    base = tmp_path / "vid"
    base.mkdir()
    _fixture(base)

    run_delivery_stage("publish", base, {})

    results = {r.get("post_id"): r for r in read_json(base / "publish.json")["results"]}
    assert results["c01-instagram-A"]["status"] != "skipped_no_copy"
    assert results["c01-instagram-A"]["calls"] >= 1


def test_the_proof_index_names_the_clip_that_has_no_post_id(tmp_path):
    base = tmp_path / "vid"
    base.mkdir()
    _fixture(base)

    run_delivery_stage("publish", base, {})

    proof = (base / "publish" / "PUBLISH_PROOF.md").read_text(encoding="utf-8")
    assert "c03" in proof, (
        "a row with no post_id still has a clip_id, and the table is the "
        "only place a reader sees that the clip was left out"
    )


def test_attribution_csv_covers_every_post_with_a_tracking_url(tmp_path):
    """attribution.csv is the bonus deliverable for per-clip UTM tracking.

    It has to survive the same partial-copy case the rest of this file
    tests: c03 never got a post_id, so it never got a tracking_url either,
    and has no row here -- the csv is about posts that were tagged, not
    every clip that was attempted."""
    base = tmp_path / "vid"
    base.mkdir()
    _fixture(base)

    run_delivery_stage("publish", base, {})

    import csv

    with (base / "attribution.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["post_id"] == "c01-instagram-A"
    assert rows[0]["clip_id"] == "c01"


def test_a_post_whose_numbers_failed_the_gate_does_not_read_as_clean(tmp_path):
    """The adapter validates the request, not whether the text is true to
    the clip. Without copywriting's verdict travelling with it, a post
    carrying a fabricated number shows up in the publish record as
    "dry_run, 0 errors"."""
    base = tmp_path / "vid"
    base.mkdir()
    _fixture(base)

    run_delivery_stage("publish", base, {})

    row = next(r for r in read_json(base / "publish.json")["results"]
               if r.get("post_id") == "c01-instagram-A")
    assert [i["code"] for i in row["copy_validation_errors"]] == ["NUMBER_HALLUCINATION"]
