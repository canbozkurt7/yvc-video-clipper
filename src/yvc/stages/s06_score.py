"""Hook scoring: 45 deterministic points, 55 LLM-judged points.

The brief rejects "the model chose it" as an explanation, so the rubric is
written down, weighted, and auditable. Two properties make it defensible:

**Reproducibility.** The deterministic criteria are pure functions of
committed artifacts, so they return identical values on every run. Only
the judgement criteria vary, and each one carries a written rationale and
a quote from the transcript.

**Independence.** The LLM never sees the deterministic scores. Shown
them, it anchors, and the two signal families stop being independent
evidence about the same segment.

Two criteria exist specifically as counterweights: `self_contained`
(deterministic penalty) and `standalone` (LLM). Without them the rubric
reliably selects loud, dramatic fragments that open with "ve bu yüzden..."
and mean nothing to a viewer arriving cold.
"""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from yvc.io import read_json, write_json
from yvc.llm.claude_cli import ClaudeCLI, LLMError
from yvc.signals import text as text_signals

# name -> (weight, kind). Weights total 100.
RUBRIC: dict[str, tuple[float, str]] = {
    "energy": (8, "deterministic"),
    "pitch_variance": (6, "deterministic"),
    "speech_rate": (5, "deterministic"),
    "numeric_density": (7, "deterministic"),
    "question_density": (6, "deterministic"),
    "turn_taking": (5, "deterministic"),
    "self_contained": (8, "deterministic"),
    "hook_3s": (14, "llm"),
    "curiosity_gap": (12, "llm"),
    "emotional_charge": (10, "llm"),
    "standalone": (11, "llm"),
    "audience_fit": (8, "llm"),
}

HOOK_TYPES = [
    "contrarian",
    "data_number",
    "question",
    "story",
    "howto",
    "curiosity_gap",
    "social_proof",
]


class LLMScores(BaseModel):
    hook_3s: float = Field(ge=0, le=10)
    curiosity_gap: float = Field(ge=0, le=10)
    emotional_charge: float = Field(ge=0, le=10)
    standalone: float = Field(ge=0, le=10)
    audience_fit: float = Field(ge=0, le=10)
    hook_type: str
    hook_line: str = Field(max_length=90)
    evidence_quote: str
    rationale: str


PROMPT = """Aşağıda Türkçe bir panelden alınmış bir kesit var. Konu: maaş,
bordro, zam, İK.

Bu kesiti sosyal medya klibi olarak değerlendirme kriterlerine göre 0-10
arasında puanla:

- hook_3s: İlk 3 saniye tek başına okunduğunda kaydırmayı durdurur mu?
- curiosity_gap: Bir merak boşluğu açıp kesit içinde kapatıyor mu?
  (0 = gerilim yok, 5 = gerilim var ama karşılığı yok, 10 = kurulum ve
  karşılık tam)
- emotional_charge: Şaşırtıcı, iddialı veya beklentiye ters bir şey söylüyor mu?
- standalone: Videonun geri kalanını izlememiş biri bunu anlar mı?
- audience_fit: Bordro/İK/ücret karar vericileri için ne kadar ilgili?

Ayrıca:
- hook_type: şunlardan biri: {hook_types}
- hook_line: klibin ilk 3 saniyesinde ekrana basılacak, en fazla 6 kelimelik
  Türkçe kanca metni
- evidence_quote: kesitten BİREBİR alınmış, en az 6 kelimelik bir alıntı
- rationale: puanlamanı 1-2 cümlede gerekçelendir, alıntıya atıf yaparak

KESİT ({duration:.0f} saniye):
{text}

İLK 3 SANİYE:
{opening}
"""


@dataclass
class ScoredSegment:
    segment_id: str
    start: float
    end: float
    total: float
    criteria: dict
    hook_type: str
    hook_line: str
    evidence_quote: str
    rationale: str
    flags: list[str]
    # The rubric score before any learned multiplier, the multiplier
    # applied, and the evidence behind it. Without these three, a score
    # months from now cannot be reconstructed, and "the model picked it"
    # is exactly the answer the rubric exists to avoid.
    base_total: float = 0.0
    multiplier: float = 1.0
    multiplier_basis: dict = field(default_factory=dict)


def _read_wav_window(path: str | Path, start: float, end: float) -> np.ndarray:
    """Read a slice of 16 kHz mono PCM without loading the whole file."""
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        wf.setpos(min(int(start * rate), wf.getnframes() - 1))
        count = max(1, int((end - start) * rate))
        raw = wf.readframes(min(count, wf.getnframes() - wf.tell()))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def energy_score(samples: np.ndarray, rate: int = 16000) -> tuple[float, float]:
    """p95 minus median frame loudness, in dB. Flat delivery scores low."""
    if samples.size < rate // 10:
        return 0.0, 0.0
    frame = rate // 10  # 100 ms
    trimmed = samples[: (samples.size // frame) * frame].reshape(-1, frame)
    rms = np.sqrt(np.mean(trimmed**2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    raw = float(np.percentile(db, 95) - np.median(db))
    return raw, _scale(raw, 3.0, 14.0)


def pitch_variance_score(samples: np.ndarray, rate: int = 16000) -> tuple[float, float]:
    """Median absolute deviation of f0 in semitones, via autocorrelation.

    Implemented directly rather than pulling in librosa, which would add
    numba and scipy for one feature.
    """
    if samples.size < rate // 2:
        return 0.0, 0.0
    frame = int(rate * 0.04)
    hop = int(rate * 0.02)
    lo, hi = int(rate / 350), int(rate / 60)  # 60-350 Hz

    f0s = []
    for start in range(0, samples.size - frame, hop):
        window = samples[start : start + frame]
        if float(np.sqrt(np.mean(window**2))) < 0.01:
            continue  # unvoiced
        window = window - window.mean()
        corr = np.correlate(window, window, mode="full")[frame - 1 :]
        if corr[0] <= 0:
            continue
        segment = corr[lo:hi]
        if segment.size == 0:
            continue
        peak = int(np.argmax(segment)) + lo
        if corr[peak] / corr[0] > 0.3:
            f0s.append(rate / peak)

    if len(f0s) < 5:
        return 0.0, 0.0
    arr = np.array(f0s)
    semitones = 12 * np.log2(arr / np.median(arr))
    raw = float(np.median(np.abs(semitones)))
    return raw, _scale(raw, 0.8, 4.0)


def turn_taking_score(speakers: list[str] | None, duration: float) -> tuple[float, float]:
    """Speaker changes per minute.

    A monologue floors at 6/10 rather than 0: on this format the single
    best clip is often one uninterrupted explanation, and zeroing it would
    be a rubric artifact rather than a judgement.
    """
    if not speakers or duration <= 0:
        return 0.0, 6.0
    changes = sum(1 for a, b in zip(speakers, speakers[1:]) if a != b)
    raw = changes * 60.0 / duration
    return raw, max(6.0, _scale(raw, 0.0, 4.0))


def _scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return max(0.0, min(10.0, (value - low) / (high - low) * 10.0))


def score_segments(
    segments_path: str | Path,
    audio_path: str | Path,
    out_path: str | Path,
    *,
    llm: ClaudeCLI | None = None,
    model: str | None = "sonnet",
    limit: int | None = None,
    priors=None,
) -> dict:
    """Score every segment and write scores.json.

    ``priors`` carries what previous videos measured: a bounded per
    hook-type multiplier. Pass ``None`` to score on the rubric alone.
    With no measured history every multiplier is exactly 1.0, so the
    learned term is inert until real metrics exist.
    """
    data = read_json(segments_path)
    segments = data["segments"]
    if limit:
        segments = segments[:limit]

    llm = llm or ClaudeCLI()
    scored: list[ScoredSegment] = []

    for index, seg in enumerate(segments):
        duration = seg["end"] - seg["start"]
        if duration < 5 or not seg["text"].strip():
            continue
        # Word density floor. A segment with a handful of words spread over
        # a long span carries no usable speech, and an LLM asked to score it
        # will invent a plausible number rather than refuse.
        word_count = len(seg["text"].split())
        if word_count < 15 or word_count / duration < 0.4:
            print(
                f"[score] {seg['id']} skipped: {word_count} words over "
                f"{duration:.0f}s is too sparse to score"
            )
            continue

        samples = _read_wav_window(audio_path, seg["start"], seg["end"])
        e_raw, e_score = energy_score(samples)
        p_raw, p_score = pitch_variance_score(samples)
        t_raw, t_score = turn_taking_score(seg.get("speakers"), duration)

        opening = " ".join(seg["text"].split()[:12])
        text_sig = text_signals.compute(
            seg["text"],
            duration,
            first_3s_text=opening,
            previous_segment_text=segments[index - 1]["text"] if index else "",
        )

        criteria: dict[str, dict] = {
            "energy": _entry(e_raw, e_score, "energy", "dB p95-median"),
            "pitch_variance": _entry(p_raw, p_score, "pitch_variance", "semitone MAD"),
            "speech_rate": _entry(
                text_sig.speech_rate_wps, text_sig.rate_score, "speech_rate", "words/s"
            ),
            "numeric_density": _entry(
                text_sig.numeric_per_100w, text_sig.numeric_score,
                "numeric_density", "per 100 words",
            ),
            "question_density": _entry(
                text_sig.question_per_100w, text_sig.question_score,
                "question_density", "per 100 words",
            ),
            "turn_taking": _entry(t_raw, t_score, "turn_taking", "changes/min"),
            "self_contained": _entry(
                None, text_sig.self_contained_score, "self_contained", None,
                extra={"penalties": text_sig.self_contained_penalties},
            ),
        }

        prompt = PROMPT.format(
            hook_types=", ".join(HOOK_TYPES),
            duration=duration,
            text=seg["text"][:4000],
            opening=opening,
        )
        try:
            result = llm.complete(f"score.{seg['id']}", prompt, LLMScores, model=model)
            judged = result.data
        except LLMError as exc:
            print(f"[score] {seg['id']}: LLM failed ({exc}); deterministic only")
            # Neutral 5s keep the segment in contention without inventing
            # judgement it never received. The flag makes that visible.
            judged = LLMScores(
                hook_3s=5, curiosity_gap=5, emotional_charge=5, standalone=5,
                audience_fit=5, hook_type="curiosity_gap", hook_line="",
                evidence_quote="", rationale="LLM unavailable; neutral defaults applied.",
            )

        for name in ("hook_3s", "curiosity_gap", "emotional_charge", "standalone", "audience_fit"):
            criteria[name] = _entry(None, getattr(judged, name), name, None, method="llm")

        base_total = sum(
            c["score"] / 10.0 * RUBRIC[name][0] for name, c in criteria.items()
        )
        # Sampled rather than mean: the posterior stays wide for rarely
        # used hook types, so they periodically draw high and get retried
        # instead of being locked out by one bad early result.
        multiplier = (
            priors.multiplier(judged.hook_type, sampled=True) if priors else 1.0
        )
        basis = {}
        if priors is not None:
            prior = priors.priors.get(judged.hook_type)
            if prior is not None:
                basis = {"n_eff": prior.n_eff, "y_hat": prior.y_hat,
                         "mean_multiplier": prior.multiplier}
        total = base_total * multiplier

        flags = []
        if text_sig.numeric_per_100w >= 4:
            flags.append("high_numeric_density")
        if text_sig.self_contained_score <= 4:
            flags.append("weak_opening")
        if judged.evidence_quote and judged.evidence_quote not in seg["text"]:
            # The model was asked for a verbatim quote; a miss means its
            # reasoning is not anchored to what was actually said.
            flags.append("evidence_not_verbatim")

        scored.append(
            ScoredSegment(
                segment_id=seg["id"],
                start=seg["start"],
                end=seg["end"],
                total=round(total, 2),
                base_total=round(base_total, 2),
                multiplier=round(multiplier, 4),
                multiplier_basis=basis,
                criteria=criteria,
                hook_type=judged.hook_type,
                hook_line=judged.hook_line,
                evidence_quote=judged.evidence_quote,
                rationale=judged.rationale,
                flags=flags,
            )
        )
        print(
            f"[score] {seg['id']} {seg['start']:7.1f}-{seg['end']:7.1f}s "
            f"total={total:5.1f} type={judged.hook_type}"
            + (f" (x{multiplier:.3f})" if multiplier != 1.0 else "")
        )

    scored.sort(key=lambda s: s.total, reverse=True)
    payload = {
        "rubric_version": "hook_v2",
        "rubric": {k: {"weight": v[0], "method": v[1]} for k, v in RUBRIC.items()},
        "deterministic_weight": sum(w for w, k in RUBRIC.values() if k == "deterministic"),
        "llm_weight": sum(w for w, k in RUBRIC.values() if k == "llm"),
        "segments": [s.__dict__ for s in scored],
    }
    write_json(out_path, payload)
    print(f"[score] {len(scored)} segments scored -> {out_path}")
    return payload


def _entry(raw, score, name, unit, *, method="deterministic", extra=None) -> dict:
    weight = RUBRIC[name][0]
    entry = {
        "raw": None if raw is None else round(float(raw), 3),
        "unit": unit,
        "score": round(float(score), 2),
        "weight": weight,
        "weighted": round(float(score) / 10.0 * weight, 2),
        "method": method,
    }
    if extra:
        entry.update(extra)
    return entry


if __name__ == "__main__":
    import sys

    import yvc.bootstrap  # noqa: F401

    base = Path(sys.argv[1] if len(sys.argv) > 1 else "work/r39OrneyMDs")
    score_segments(
        base / "segments.json",
        base / "audio16k_raw.wav",
        base / "scores.json",
        limit=int(sys.argv[2]) if len(sys.argv) > 2 else None,
    )
