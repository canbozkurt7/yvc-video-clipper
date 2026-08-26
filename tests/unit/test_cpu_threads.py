"""Thread count is discovered, not configured.

Setting this by hand is a step that has to be repeated on every machine
and gets it wrong silently when forgotten -- the value that suits a
4-core laptop leaves a third of a 6-core desktop idle. Physical core
count is a fact about the machine, so the machine should supply it.

Physical, not logical: this is an int8 GEMM workload that saturates the
execution units, and SMT/hyperthreading siblings contend for them rather
than adding throughput.
"""

from __future__ import annotations

from yvc.stages import s02_transcribe
from yvc.stages.s02_transcribe import resolve_cpu_threads


def test_an_explicit_setting_always_wins():
    """Auto-detection must never override a deliberate choice, including
    on a machine where it would pick something different."""
    assert resolve_cpu_threads(6, physical=4) == 6
    assert resolve_cpu_threads(2, physical=16) == 2


def test_auto_uses_the_physical_core_count():
    assert resolve_cpu_threads("auto", physical=6) == 6
    assert resolve_cpu_threads(None, physical=4) == 4


def test_auto_falls_back_when_the_count_is_unknown(monkeypatch):
    """psutil is an optional dependency and cpu_count can return None.

    Detection has to be stubbed to reach this path. ``physical=None``
    means "not supplied, go and detect", not "detection came back
    empty", so passing it alone just ran the real probe -- which
    returned 4 on the 4-core laptop this was written on and made the
    assertion pass for the wrong reason. On a 6-core machine it returns
    6 and the test fails while the code is perfectly correct.
    """
    monkeypatch.setattr(s02_transcribe, "physical_cores", lambda: None)
    assert resolve_cpu_threads("auto", physical=None) == 4


def test_the_result_is_always_at_least_one():
    assert resolve_cpu_threads("auto", physical=0) >= 1
    assert resolve_cpu_threads(0, physical=8) >= 1


def test_a_very_large_machine_is_capped():
    """Whisper stops scaling well past a point, and an unbounded thread
    count on a 64-core server wastes memory bandwidth on contention."""
    assert resolve_cpu_threads("auto", physical=64) <= 16
