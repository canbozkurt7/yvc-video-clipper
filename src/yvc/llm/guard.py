"""Refuse to hand back an artifact that is mostly guesswork.

Every LLM stage degrades rather than dies when a single call fails: a
window that fails falls back to pause splitting, a segment that fails is
scored with neutral fives, a clip that fails is written out as a failed
row. Per item that is right -- one bad call should not cost an hour of
transcription.

In bulk it is not right at all. When the `claude` CLI stopped answering
partway through a run here, all 26 segments took the neutral-five path,
every hook line came back empty, selection could locate none of them and
the pipeline wrote 0 clips and 0 posts -- and exited 0, reporting
success. Nothing in the artifacts said the scores were never judged. A
degraded run has to be distinguishable from a clean one, and the point
where "degraded" becomes "meaningless" is a ratio, which is what
``runtime.min_success_ratio`` in config.yaml has always described and
what this enforces.
"""

from __future__ import annotations


class LLMSuccessRateError(RuntimeError):
    """Too few LLM calls in a stage came back to trust the output."""


def require_success_ratio(
    stage: str,
    succeeded: int,
    attempted: int,
    minimum: float,
) -> None:
    """Raise when ``succeeded / attempted`` falls below ``minimum``.

    ``attempted`` counts only the items that actually reached the model,
    so items filtered out beforehand -- a segment too sparse to score --
    neither help nor hurt the ratio.

    Zero attempts is not a failure: a stage with nothing to do has
    nothing to be degraded about, and dividing by it would turn an empty
    input into a crash.
    """
    if attempted <= 0 or minimum <= 0:
        return

    ratio = succeeded / attempted
    if ratio >= minimum:
        return

    raise LLMSuccessRateError(
        f"{stage}: only {succeeded}/{attempted} LLM calls succeeded "
        f"({ratio:.0%}), below the {minimum:.0%} required by "
        f"runtime.min_success_ratio. The artifact this would produce is "
        f"mostly fallback values, not judgement. Common causes: the "
        f"`claude` CLI is not signed in, or the account hit its usage "
        f"limit mid-run. Re-run once it answers again -- successful "
        f"calls are cached, so the completed work is not repeated."
    )
