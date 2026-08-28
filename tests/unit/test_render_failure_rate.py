"""Five failed clips is not a partial deliverable.

The incident: this machine's ffmpeg was upgraded to 9.0, which removed
`-filter_complex_script`. Every clip failed with "Unrecognized option",
the stage printed "0 ok, 5 failed", wrote render.json, and recorded
itself in the manifest as ok -- so the next run skipped it as up to date
and publish went looking for media that had never been encoded.
"""

from __future__ import annotations

import pytest

from yvc.io import read_json, write_json
from yvc.stages import s08_render
from yvc.stages.s08_render import RenderFailureRateError, RenderResult, render_all


@pytest.fixture
def base(tmp_path):
    work = tmp_path / "vid"
    work.mkdir(parents=True)
    write_json(work / "clips.json", {"clips": [
        {"clip_id": f"c{i:02d}", "aspect": "9:16", "start": 0.0, "end": 30.0,
         "duration": 30.0, "render_variant": "plain"}
        for i in range(1, 6)
    ]})
    write_json(work / "transcript.json", {"segments": []})
    return work


def _outcomes(monkeypatch, statuses: list[str]) -> None:
    monkeypatch.setattr(s08_render, "probe_encoder", lambda *a, **k: "libx264")
    calls = iter(statuses)

    def fake_render_clip(clip, **kwargs):
        status = next(calls)
        return RenderResult(
            clip["clip_id"], clip["aspect"], status,
            path=str(kwargs["out_root"] / clip["clip_id"] / "clip.mp4")
            if status == "ok" else None,
            error=None if status == "ok" else "Unrecognized option 'x'.",
        )

    monkeypatch.setattr(s08_render, "render_clip", fake_render_clip)


def test_a_render_where_everything_failed_is_refused(monkeypatch, base):
    _outcomes(monkeypatch, ["failed"] * 5)

    with pytest.raises(RenderFailureRateError, match="0/5"):
        render_all(base, min_success_ratio=0.6)


def test_the_refused_render_still_leaves_its_evidence(monkeypatch, base):
    _outcomes(monkeypatch, ["failed"] * 5)

    with pytest.raises(RenderFailureRateError) as excinfo:
        render_all(base, min_success_ratio=0.6)

    payload = read_json(base / "render.json")
    assert payload["failed"] == 5, "render.json is written before the raise"
    assert "Unrecognized option" in str(excinfo.value), (
        "the first error belongs in the message; the failure is an "
        "environment problem and the ffmpeg text is what names it"
    )


def test_one_clip_failing_out_of_five_still_delivers(monkeypatch, base):
    _outcomes(monkeypatch, ["ok", "ok", "failed", "ok", "ok"])

    payload = render_all(base, min_success_ratio=0.6)

    assert (payload["ok"], payload["failed"]) == (4, 1), (
        "per clip the rule is unchanged: one failure must not cost the run"
    )


def test_ratio_zero_restores_the_old_permissive_behaviour(monkeypatch, base):
    _outcomes(monkeypatch, ["failed"] * 5)

    payload = render_all(base, min_success_ratio=0.0)

    assert payload["ok"] == 0
