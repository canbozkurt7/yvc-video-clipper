"""The LLM subprocess must always be killable.

A no-touch pipeline that can hang forever is not automated, it is
unattended. This happened for real: on Windows ``claude`` is an npm .cmd
shim, so the command runs as ``cmd.exe -> claude``. ``subprocess.run``'s
timeout kills only ``cmd.exe``; the orphaned grandchild keeps the
inherited stdout/stderr pipes open, and the post-kill ``communicate()``
blocks waiting for a writer that never closes. A 300 s timeout became an
18-minute stall with no progress and no error.

These tests use a real subprocess -- a mock cannot reproduce the bug,
because the bug is in how the OS hands out pipe handles.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from yvc.llm.claude_cli import ClaudeCLI, LLMTransientError, _kill_tree

# A child that spawns a grandchild inheriting its pipes, then exits.
# Killing only the child leaves the grandchild holding stdout.
SPAWNER = (
    "import subprocess,sys;"
    "subprocess.Popen([sys.executable,'-c',"
    "\"import time;time.sleep(300)\"]);"
    "import time;time.sleep(300)"
)


def test_timeout_raises_promptly_instead_of_hanging():
    """The core regression: a slow child must produce an error, fast."""
    cli = ClaudeCLI(timeout_s=3)
    cli._resolved = [sys.executable, "-c", SPAWNER, "--"]

    started = time.time()
    with pytest.raises(LLMTransientError, match="timed out"):
        cli._invoke("anything", None)
    elapsed = time.time() - started

    # Generous, but far below the minutes-long hang this replaces.
    assert elapsed < 40, f"timeout took {elapsed:.1f}s -- the hang is back"


def test_timeout_is_transient_so_the_pipeline_retries():
    """Classified transient: a timeout is worth another attempt, unlike a
    schema violation."""
    cli = ClaudeCLI(timeout_s=2)
    cli._resolved = [sys.executable, "-c", "import time;time.sleep(120)", "--"]
    with pytest.raises(LLMTransientError) as info:
        cli._invoke("anything", None)
    assert info.value.transient


def test_kill_tree_terminates_a_running_process():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(120)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _kill_tree(proc)
        proc.wait(timeout=30)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:  # pragma: no cover - only on failure
            proc.kill()


def test_kill_tree_on_an_exited_process_is_a_no_op():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    _kill_tree(proc)  # must not raise


def test_successful_invocation_still_returns_stdout():
    """The rewrite must not break the normal path."""
    cli = ClaudeCLI(timeout_s=60)
    cli._resolved = [sys.executable, "-c", "print('{\"ok\": true}')", "--"]
    assert "ok" in cli._invoke("prompt", None)


def test_nonzero_exit_is_reported_with_stderr():
    cli = ClaudeCLI(timeout_s=60)
    cli._resolved = [
        sys.executable, "-c",
        "import sys;sys.stderr.write('boom');sys.exit(3)", "--",
    ]
    with pytest.raises(LLMTransientError, match="exited 3"):
        cli._invoke("prompt", None)


# --- configuration is actually read ---------------------------------


def test_from_config_reads_the_llm_section():
    """`llm:` in config.yaml was decorative: every stage built
    ClaudeCLI() with hard-coded defaults, so editing timeout_s or cache
    changed nothing."""
    cli = ClaudeCLI.from_config(
        {"timeout_s": 123, "max_attempts": 7, "cache": True, "cache_dir": "x/y"}
    )
    assert cli.timeout_s == 123
    assert cli.max_attempts == 7
    assert str(cli.cache_dir).replace("\\", "/") == "x/y"


def test_from_config_can_disable_caching():
    assert ClaudeCLI.from_config({"cache": False}).cache_dir is None


def test_from_config_defaults_are_sane_when_section_is_absent():
    cli = ClaudeCLI.from_config(None)
    assert cli.timeout_s >= 300      # room for ~99 s time-to-first-token
    assert cli.cache_dir is not None  # caching on by default


def test_real_config_file_is_wired_through():
    """Guards the whole chain: config.yaml -> from_config -> ClaudeCLI."""
    import pathlib

    import yaml

    path = pathlib.Path("config/config.yaml")
    if not path.exists():  # pragma: no cover - depends on invocation cwd
        pytest.skip("config.yaml not reachable from this working directory")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    cli = ClaudeCLI.from_config(config.get("llm"))
    assert cli.timeout_s == config["llm"]["timeout_s"]
    assert cli.max_attempts == config["llm"]["max_attempts"]
