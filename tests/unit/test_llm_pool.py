"""The pool's ordering guarantee is what the stage artifacts rest on.

Segmentation merges window results with ``setdefault`` and scoring breaks
ties by insertion order in a stable sort, so a pool that returned results
as they completed would make segments.json and scores.json vary between
identical runs. That property is worth testing directly rather than
inferring it from a green pipeline.
"""

from __future__ import annotations

import threading
import time

import pytest

from yvc.llm.pool import concurrency_of, map_ordered


def test_results_are_input_ordered_not_completion_ordered():
    """The last item finishes first; the output must not notice."""

    def fn(index: int, item: int) -> int:
        time.sleep(0.02 * (5 - item))
        return item

    assert map_ordered(fn, [0, 1, 2, 3, 4], 5) == [0, 1, 2, 3, 4]


def test_the_work_actually_overlaps():
    """A pool that preserved order by running serially would pass every
    other test in this file."""
    lock = threading.Lock()
    live = 0
    peak = 0

    def fn(index: int, item: int) -> int:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return item

    map_ordered(fn, list(range(6)), 3)
    assert peak > 1, "nothing ran concurrently"
    assert peak <= 3, f"pool exceeded its width: {peak}"


@pytest.mark.parametrize("concurrency", [0, 1])
def test_serial_path_starts_no_threads(concurrency: int):
    """``concurrency: 1`` has to be a true rollback, not a narrow pool."""
    main = threading.current_thread().name
    seen: list[str] = []

    def fn(index: int, item: int) -> int:
        seen.append(threading.current_thread().name)
        return item

    assert map_ordered(fn, [1, 2, 3], concurrency) == [1, 2, 3]
    assert seen == [main, main, main]


def test_the_index_travels_with_the_item():
    """Scoring needs it to reach the previous segment's text."""
    assert map_ordered(lambda i, x: (i, x), ["a", "b"], 2) == [(0, "a"), (1, "b")]


def test_an_unexpected_exception_propagates():
    """Callers catch LLMError themselves; anything else must fail the stage."""

    def fn(index: int, item: int) -> int:
        if item == 2:
            raise ValueError("boom")
        return item

    with pytest.raises(ValueError, match="boom"):
        map_ordered(fn, [1, 2, 3], 3)


def test_empty_and_single_item_inputs():
    assert map_ordered(lambda i, x: x, [], 4) == []
    assert map_ordered(lambda i, x: x, [7], 4) == [7]


class _Engine:
    def __init__(self, concurrency):
        self.concurrency = concurrency


def test_concurrency_of_reads_the_engine():
    assert concurrency_of(_Engine(4)) == 4


def test_concurrency_of_defaults_to_serial_without_the_field():
    """A test double implements complete() and nothing else."""
    assert concurrency_of(object()) == 1


@pytest.mark.parametrize("value", [0, -3, None])
def test_concurrency_of_floors_at_one(value):
    assert concurrency_of(_Engine(value)) == 1
