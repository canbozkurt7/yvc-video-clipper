"""One segment's hook score, rendered to be read.

The rubric is a required deliverable and `scores.json` already carries
every part of it: each criterion's raw measurement, its unit, the score
it earned, its weight, and for the LLM half a written rationale plus a
verbatim quote. But it carries them as nested JSON, which answers
"is it recorded?" and not "why did this segment win?".

This renders the same record as a scorecard: deterministic and judged
criteria separated, raw measurements shown next to the scores they
produced, and the evidence quote last so it can be checked against the
clip itself. Nothing here is computed -- it is a view.
"""

from __future__ import annotations

from pathlib import Path

from yvc.io import read_json

BAR_WIDTH = 10


def bar(score: float, width: int = BAR_WIDTH) -> str:
    """A 0-10 score as a filled bar. Readable at a glance and, unlike a
    number, comparable between two scorecards side by side."""
    filled = int(round(max(0.0, min(10.0, score)) / 10 * width))
    return "#" * filled + "." * (width - filled)


def _raw(criterion: dict) -> str:
    raw, unit = criterion.get("raw"), criterion.get("unit")
    if raw is None:
        return ""
    return f"{raw:g} {unit}".strip() if unit else f"{raw:g}"


def render(segment: dict, *, threshold: float | None = None) -> str:
    """The scorecard for one scored segment."""
    lines: list[str] = []
    total = segment.get("total", 0.0)
    base = segment.get("base_total", total)
    multiplier = segment.get("multiplier", 1.0)

    lines.append("=" * 64)
    verdict = ""
    if threshold is not None:
        verdict = "  PASS" if total >= threshold else "  below threshold"
    lines.append(
        f"{segment['segment_id']}   {segment['start']:.1f}-{segment['end']:.1f}s"
        f"   total {total:.1f}/100{verdict}"
    )
    lines.append(f"hook type: {segment.get('hook_type', '?')}")
    lines.append("=" * 64)

    for method, title, weight_total in (
        ("deterministic", "DETERMINISTIC  (measured from waveform and text)", 45),
        ("llm", "JUDGED  (each carries a written rationale)", 55),
    ):
        picked = [
            (name, c) for name, c in segment.get("criteria", {}).items()
            if c.get("method") == method
        ]
        if not picked:
            continue
        earned = sum(c.get("weighted", 0.0) for _, c in picked)
        lines.append("")
        lines.append(f"-- {title}")
        lines.append(f"   {'criterion':22s} {'score':>6s}  {'':{BAR_WIDTH}s} "
                     f"{'pts':>6s}  measured")
        for name, c in picked:
            lines.append(
                f"   {name:22s} {c.get('score', 0):6.1f}  {bar(c.get('score', 0))} "
                f"{c.get('weighted', 0):5.1f}/{c.get('weight', 0):<2}  {_raw(c)}"
            )
        lines.append(f"   {'':22s} {'':6s}  {'':{BAR_WIDTH}s} "
                     f"{earned:5.1f}/{weight_total}")

    if abs(multiplier - 1.0) > 1e-9:
        lines.append("")
        lines.append(f"-- LEARNED ADJUSTMENT  (from previous videos' measured results)")
        lines.append(f"   rubric score {base:.1f}  x  {multiplier:.3f}  =  {total:.1f}")
        basis = segment.get("multiplier_basis") or {}
        if basis:
            lines.append(
                f"   basis: n_eff={basis.get('n_eff')}, y_hat={basis.get('y_hat')}"
            )

    hook = (segment.get("hook_line") or "").strip()
    quote = (segment.get("evidence_quote") or "").strip()
    rationale = (segment.get("rationale") or "").strip()

    if rationale:
        lines.append("")
        lines.append("-- WHY  (the model's own reasoning, recorded at scoring time)")
        for chunk in _wrap(rationale, 60):
            lines.append(f"   {chunk}")

    if hook:
        lines.append("")
        lines.append(f"-- HOOK LINE   {hook!r}")
    if quote:
        lines.append("")
        lines.append("-- EVIDENCE  (must be checkable against the clip's own audio)")
        for chunk in _wrap(quote, 60):
            lines.append(f"   {chunk}")

    flags = segment.get("flags") or []
    if flags:
        lines.append("")
        lines.append(f"-- FLAGS  {', '.join(flags)}")
        if "evidence_not_verbatim" in flags:
            lines.append("   the model paraphrased its own quote; selection falls")
            lines.append("   back to fuzzy matching to locate the real timestamp")

    lines.append("=" * 64)
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def show(base: str | Path, segment_id: str | None = None,
         *, threshold: float | None = None) -> str:
    """Render one segment, or the highest-scoring one by default."""
    data = read_json(Path(base) / "scores.json")
    segments = data.get("segments", [])
    if not segments:
        return "no scored segments"

    if segment_id:
        chosen = next(
            (s for s in segments if s["segment_id"] == segment_id), None
        )
        if chosen is None:
            known = ", ".join(s["segment_id"] for s in segments[:6])
            return f"no segment {segment_id!r}. first few: {known} ..."
    else:
        chosen = max(segments, key=lambda s: s.get("total", 0))

    return render(chosen, threshold=threshold)
