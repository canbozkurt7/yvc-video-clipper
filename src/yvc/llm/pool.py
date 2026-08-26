"""Bounded-concurrency helper for the LLM-backed stages.

Segmentation, scoring and copywriting each make one `claude` call per
item, and those calls spend most of their wall clock waiting rather than
computing -- measured on this project at ~99 s of a ~122 s copywriting
call being time-to-first-token. Run one at a time, the machine sits idle
through the majority of two stages that together cost 38 minutes.

Threads rather than processes: what is being overlapped is
``subprocess.communicate()``, which releases the GIL, so threads buy the
whole overlap without pickling anything across a process boundary.

The ordering guarantee is the reason this is a module rather than three
inline ``ThreadPoolExecutor`` blocks. Results are returned in *input*
order no matter what order they complete in, because every caller merges
into a structure where arrival order is load-bearing: ``setdefault`` on a
boundary title, insertion order breaking ties in a stable sort. A pool
that yielded results as they finished would make those artifacts differ
between two runs of the same input, which is exactly the property the
resume logic and the caching are built to preserve.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def concurrency_of(llm: object) -> int:
    """Pool width to use for an LLM engine.

    ``getattr`` rather than a plain attribute read because a stage's
    contract with its engine is ``complete()`` and nothing else. Test
    doubles and any other implementation should not have to grow a field
    to keep working, and serial is the safe thing to assume when none is
    declared.
    """
    return max(1, int(getattr(llm, "concurrency", 1) or 1))


def map_ordered(
    fn: Callable[[int, T], R],
    items: list[T],
    concurrency: int,
) -> list[R]:
    """Apply ``fn(index, item)`` across ``items``; results in input order.

    ``concurrency <= 1`` takes a plain sequential loop rather than a
    one-worker pool, so ``llm.concurrency: 1`` is a genuine rollback to
    the previous behaviour and not merely a pool that happens to be
    narrow.

    Exceptions propagate from the first item that raised, in input order.
    Callers already catch ``LLMError`` per item and degrade, so anything
    reaching here is unexpected and should fail the stage -- which is
    what the serial version did too.
    """
    if concurrency <= 1 or len(items) <= 1:
        return [fn(index, item) for index, item in enumerate(items)]

    with ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="yvc-llm"
    ) as pool:
        futures = [pool.submit(fn, index, item) for index, item in enumerate(items)]
        # Resolving in submission order blocks on each in turn, and that
        # is precisely what makes the returned list input-ordered.
        return [future.result() for future in futures]
