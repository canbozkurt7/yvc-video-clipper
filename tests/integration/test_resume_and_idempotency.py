"""Orchestrator resume, idempotency and failure isolation.

The brief grades "can the system be re-run?" directly, so these are the
properties worth pinning:

* Running twice does no redundant work.
* Changing one config value re-runs that stage and its descendants, and
  nothing upstream -- specifically, editing a copywriting weight must not
  invalidate an hour of transcription.
* A stage that fails is recorded as failed and does not corrupt the
  manifest for stages that already succeeded.

These use the real fingerprint machinery rather than mocking it, because
the fingerprint logic is exactly what could silently break.
"""

from __future__ import annotations

import json

import pytest

from yvc.cli import (
    CONFIG_KEYS,
    DEPENDS,
    OUTPUTS,
    STAGES,
    Manifest,
    config_hash,
    outputs_present,
    stage_fingerprint,
)

BASE_CONFIG = {
    "source": {"format": "299+140"},
    "whisper": {"model": "small", "beam_size": 1},
    "turkish": {"min_diacritic_density": 35},
    "segment": {"window_s": 480},
    "score": {"threshold": 55},
    "select": {"vertical": {"count": 3}},
    "render": {"encoder": "libx264"},
    "reframe": {"deadzone_frac": 0.06},
    "subtitles": {"font_size": 74},
    "copy": {"bilingual": True},
    "llm": {"model_main": "sonnet"},
    "publish": {"mode": "dry_run"},
    "metrics": {"windows": ["T+24h"]},
    "feedback": {"shrinkage_k": 8},
}


def _complete(manifest: Manifest, config: dict, stages=None) -> None:
    """Mark stages done with correct fingerprints, in dependency order."""
    for name in (stages or STAGES):
        fingerprint = stage_fingerprint(name, config, manifest)
        manifest.stage(name).update(
            {"status": "ok", "fingerprint": fingerprint, "duration_s": 1.0}
        )


def test_second_run_skips_everything(tmp_path):
    manifest = Manifest(tmp_path / "manifest.json")
    _complete(manifest, BASE_CONFIG)

    for name in STAGES:
        assert manifest.stage(name)["fingerprint"] == stage_fingerprint(
            name, BASE_CONFIG, manifest
        ), f"{name} would needlessly re-run"


def test_changing_copy_config_does_not_invalidate_transcription(tmp_path):
    """The expensive-stage protection. Editing copy weights must not cost
    an hour of Whisper."""
    manifest = Manifest(tmp_path / "manifest.json")
    _complete(manifest, BASE_CONFIG)

    before = {n: manifest.stage(n)["fingerprint"] for n in STAGES}

    changed = json.loads(json.dumps(BASE_CONFIG))
    changed["copy"]["bilingual"] = False

    # Upstream of copywrite must be untouched.
    for name in ("acquire", "transcribe", "turkish", "segment", "score", "select"):
        assert stage_fingerprint(name, changed, manifest) == before[name], (
            f"{name} was invalidated by a copywriting change"
        )

    # copywrite itself must change.
    assert stage_fingerprint("copywrite", changed, manifest) != before["copywrite"]


def test_changing_whisper_config_invalidates_downstream(tmp_path):
    manifest = Manifest(tmp_path / "manifest.json")
    _complete(manifest, BASE_CONFIG)
    before = {n: manifest.stage(n)["fingerprint"] for n in STAGES}

    changed = json.loads(json.dumps(BASE_CONFIG))
    changed["whisper"]["model"] = "large-v3"

    assert stage_fingerprint("transcribe", changed, manifest) != before["transcribe"]
    assert stage_fingerprint("acquire", changed, manifest) == before["acquire"]

    # Recompute forward: every descendant must shift, since fingerprints
    # chain through dependencies.
    fresh = Manifest(tmp_path / "m2.json")
    _complete(fresh, changed)
    for name in ("turkish", "segment", "score", "select", "render", "copywrite"):
        assert fresh.stage(name)["fingerprint"] != before[name], (
            f"{name} should have been invalidated by a model change"
        )


def test_dependency_graph_is_acyclic_and_complete():
    seen: set[str] = set()
    for name in STAGES:
        for dependency in DEPENDS.get(name, []):
            assert dependency in STAGES, f"{name} depends on unknown {dependency}"
            assert dependency in seen, (
                f"{name} depends on {dependency}, which runs later"
            )
        seen.add(name)


def test_every_stage_declares_outputs_and_config_keys():
    for name in STAGES:
        assert name in OUTPUTS and OUTPUTS[name], f"{name} declares no outputs"
        assert name in CONFIG_KEYS, f"{name} declares no config dependency"


def test_outputs_present_requires_all_declared_files(tmp_path):
    (tmp_path / "transcript.json").write_text("{}", encoding="utf-8")
    assert outputs_present(tmp_path, "transcribe")
    assert not outputs_present(tmp_path, "acquire")  # source.mp4 missing


def test_manifest_survives_corruption(tmp_path):
    """A truncated manifest must not crash the next run."""
    path = tmp_path / "manifest.json"
    path.write_text('{"stages": {"transcribe": ', encoding="utf-8")
    manifest = Manifest.load(path)
    assert manifest.data.get("stages") == {}


def test_failed_stage_does_not_clear_completed_ones(tmp_path):
    manifest = Manifest(tmp_path / "manifest.json")
    _complete(manifest, BASE_CONFIG, ["acquire", "transcribe"])
    manifest.stage("segment").update(
        {"status": "failed", "fingerprint": "x", "error": "boom"}
    )
    manifest.save()

    reloaded = Manifest.load(tmp_path / "manifest.json")
    assert reloaded.stage("transcribe")["status"] == "ok"
    assert reloaded.stage("segment")["status"] == "failed"


def test_config_hash_ignores_unrelated_sections():
    a = config_hash(BASE_CONFIG, ["whisper"])
    changed = json.loads(json.dumps(BASE_CONFIG))
    changed["publish"]["mode"] = "live"
    assert config_hash(changed, ["whisper"]) == a


@pytest.mark.parametrize("stage", STAGES)
def test_fingerprint_is_deterministic(stage, tmp_path):
    manifest = Manifest(tmp_path / "manifest.json")
    _complete(manifest, BASE_CONFIG)
    first = stage_fingerprint(stage, BASE_CONFIG, manifest)
    second = stage_fingerprint(stage, BASE_CONFIG, manifest)
    assert first == second
