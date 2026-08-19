"""Transcription with incremental checkpointing.

This stage owns 63-92 minutes of the pipeline's runtime and 3.2-3.6 GB of
its peak memory, which makes two things non-negotiable:

**Checkpointing.** ``model.transcribe`` returns a generator, so segments
arrive as they are decoded. Each one is appended to a JSONL file and
flushed every N segments. A crash costs at most ~40 seconds of audio
rather than the whole hour. On resume the committed portion is read back
and decoding restarts from that offset.

**Exclusive memory.** Nothing else may run concurrently. The tempting
optimisation -- generating the face-detection proxy while Whisper works
-- would put a 500 MB ffmpeg process next to a 3.6 GB model on a machine
with 7.7 GB total, and paging to a laptop SSD would cost far more than
the few minutes it saves.

The RAM preflight steps down the model ladder rather than dying, and
records the downgrade so a degraded run is never mistaken for a clean one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from yvc.bootstrap import child_env
from yvc.io import append_jsonl, read_jsonl, write_json
from yvc.turkish.casing import nfc

# Approximate steady-state resident memory per model, int8 on CPU.
MODEL_RAM_MB = {
    "tiny": 300,
    "base": 400,
    "small": 900,
    "medium": 2000,
    "large-v3": 3600,
}

# Domain-loaded prompt. This primes the decoder toward correctly
# diacriticised vocabulary it otherwise mangles (bordro, kıdem, İK) and
# asks explicitly for punctuation, which the segmentation stage depends
# on for sentence boundaries. Deliberately free of example sentences --
# those leak into the output.
INITIAL_PROMPT_TR = (
    "Bu, Türkçe bir panel/podcast kaydıdır. Konuşmacılar: Umut Özbağcı ve "
    "Emrah Safa Gürkan. Konu: maaş, bordro, zam, asgari ücret, enflasyon, "
    "SGK, vergi dilimi, kıdem tazminatı, İK, insan kaynakları, brüt, net, "
    "yan haklar, performans primi. Noktalama işaretlerini ve Türkçe "
    "karakterleri (ç, ğ, ı, İ, ö, ş, ü) doğru kullan."
)


@dataclass
class TranscribeConfig:
    model: str = "small"
    compute_type: str = "int8"
    fallback_ladder: list[str] = field(
        default_factory=lambda: ["large-v3", "medium", "small"]
    )
    cpu_threads: int = 4
    num_workers: int = 1
    beam_size: int = 1
    language: str = "tr"
    checkpoint_every: int = 10
    vad_filter: bool = True
    vad_threshold: float = 0.5
    min_speech_ms: int = 250
    min_silence_ms: int = 700
    speech_pad_ms: int = 200


def choose_model(cfg: TranscribeConfig) -> tuple[str, str | None]:
    """Pick the largest model that fits available RAM.

    Returns (model, downgrade_reason). A downgrade is surfaced rather than
    silently applied -- a run transcribed with `small` produces materially
    worse Turkish diacritics and the report must be able to say so.
    """
    try:
        import psutil

        available = psutil.virtual_memory().available / (1024 * 1024)
    except Exception:
        return cfg.model, None

    ladder = cfg.fallback_ladder or [cfg.model]
    if cfg.model in ladder:
        ladder = ladder[ladder.index(cfg.model):]

    for candidate in ladder:
        needed = MODEL_RAM_MB.get(candidate, 3600) * 1.35
        if available >= needed:
            if candidate != cfg.model:
                return candidate, (
                    f"{cfg.model} needs ~{int(MODEL_RAM_MB.get(cfg.model, 3600) * 1.35)} MB "
                    f"but only {int(available)} MB was available"
                )
            return candidate, None

    smallest = ladder[-1]
    return smallest, f"only {int(available)} MB available; forced to {smallest}"


def probe_model(name: str, compute_type: str = "int8", threads: int = 4) -> bool:
    """Try loading a model in a child process; return True if it survives.

    An out-of-memory failure inside CTranslate2 arrives as a native
    segmentation fault, not a Python MemoryError. It terminates the
    interpreter outright, so an in-process try/except cannot catch it --
    the fallback ladder has to attempt each tier in a separate process
    and inspect the exit code. Learned the hard way: large-v3 segfaulted
    with 2.9 GB free and took the whole run with it.
    """
    import subprocess
    import sys

    code = (
        "from faster_whisper import WhisperModel;"
        f"WhisperModel({name!r}, device='cpu', compute_type={compute_type!r},"
        f" cpu_threads={threads}, num_workers=1);"
        "print('OK')"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env(),
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0 and "OK" in (proc.stdout or "")


def transcribe(
    audio_path: str | Path,
    out_dir: str | Path,
    cfg: TranscribeConfig | None = None,
    *,
    resume: bool = True,
    progress_every: int = 25,
) -> dict:
    """Transcribe an audio file, checkpointing as it goes."""
    from faster_whisper import WhisperModel

    cfg = cfg or TranscribeConfig()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_dir / "transcript.partial.jsonl"
    final = out_dir / "transcript.json"

    model_name, downgrade = cfg.model, None

    committed: list[dict] = read_jsonl(partial) if resume else []
    if not resume and partial.exists():
        partial.unlink()

    resume_from = 0.0
    if committed:
        resume_from = max(row["end"] for row in committed)
        print(
            f"[transcribe] resuming from {resume_from:.1f}s "
            f"({len(committed)} segments already committed)"
        )

    print(f"[transcribe] loading {model_name} ({cfg.compute_type}) ...")
    if downgrade:
        print(f"[transcribe] WARNING downgraded model: {downgrade}")

    # Attempt the requested model, stepping down only on a real failure.
    # Predicting the limit from psutil.available is unreliable on Windows,
    # which reports reclaimable file cache as unavailable -- a freshly
    # downloaded 3 GB model makes the estimate pessimistic by exactly the
    # amount that matters. Trying and catching is both more accurate and
    # more honest: a downgrade then reflects what actually failed.
    ladder = cfg.fallback_ladder or [cfg.model]
    if cfg.model in ladder:
        ladder = ladder[ladder.index(cfg.model):]
    elif cfg.model not in ladder:
        ladder = [cfg.model, *ladder]

    # Probe out-of-process first so a native crash costs a subprocess
    # rather than the run.
    probed = [c for c in ladder if probe_model(c, cfg.compute_type, cfg.cpu_threads)]
    if not probed:
        raise RuntimeError(
            f"no model in {ladder} could be loaded; free more memory and retry"
        )
    if probed[0] != ladder[0]:
        print(
            f"[transcribe] {ladder[0]} did not survive a load probe; "
            f"using {probed[0]}",
            flush=True,
        )
    ladder = probed

    model = None
    for candidate in ladder:
        try:
            load_started = time.time()
            model = WhisperModel(
                candidate,
                device="cpu",
                compute_type=cfg.compute_type,
                cpu_threads=cfg.cpu_threads,
                num_workers=cfg.num_workers,
            )
            if candidate != cfg.model:
                downgrade = f"{cfg.model} could not be loaded; fell back to {candidate}"
                print(f"[transcribe] WARNING {downgrade}", flush=True)
            model_name = candidate
            print(
                f"[transcribe] model {candidate} ready in "
                f"{time.time() - load_started:.0f}s",
                flush=True,
            )
            break
        except (MemoryError, RuntimeError, OSError) as exc:
            print(
                f"[transcribe] {candidate} failed to load ({type(exc).__name__}: "
                f"{str(exc)[:120]}); trying next tier",
                flush=True,
            )
            model = None

    if model is None:
        raise RuntimeError(f"no model in {ladder} could be loaded")

    kwargs = dict(
        language=cfg.language,
        task="transcribe",
        beam_size=cfg.beam_size,
        # Measured on this CPU: the full six-step ladder can spend minutes
        # retrying a single difficult window. Three steps keep the
        # hallucination guard without the pathological stalls.
        temperature=[0.0, 0.2, 0.4],
        word_timestamps=True,
        # The single most important flag. Left enabled, large-v3 on a
        # 60-minute Turkish panel eventually enters a repetition loop and
        # poisons every segment after it.
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        initial_prompt=INITIAL_PROMPT_TR,
        hallucination_silence_threshold=2.0,
    )
    if cfg.vad_filter:
        kwargs["vad_filter"] = True
        kwargs["vad_parameters"] = dict(
            threshold=cfg.vad_threshold,
            min_speech_duration_ms=cfg.min_speech_ms,
            min_silence_duration_ms=cfg.min_silence_ms,
            speech_pad_ms=cfg.speech_pad_ms,
        )
    if resume_from > 0:
        kwargs["clip_timestamps"] = [resume_from, 0]

    started = time.time()
    segments, info = model.transcribe(str(audio_path), **kwargs)

    duration = float(getattr(info, "duration", 0.0) or 0.0)
    print(f"[transcribe] audio {duration:.0f}s, decoding ...")

    rows = list(committed)
    since_flush = 0
    seen_end = resume_from

    for segment in segments:
        # Dedupe on resume: clip_timestamps can re-emit a boundary segment.
        if segment.end <= seen_end + 0.2 and rows:
            continue
        seen_end = segment.end

        row = {
            "id": len(rows),
            "start": round(segment.start, 3),
            "end": round(segment.end, 3),
            "text": nfc(segment.text.strip()),
            "avg_logprob": round(getattr(segment, "avg_logprob", 0.0), 4),
            "no_speech_prob": round(getattr(segment, "no_speech_prob", 0.0), 4),
            "compression_ratio": round(getattr(segment, "compression_ratio", 0.0), 4),
            "words": [
                {
                    "w": nfc(w.word.strip()),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "p": round(w.probability, 3),
                }
                for w in (segment.words or [])
            ],
        }
        rows.append(row)
        since_flush += 1

        # fsync every N segments rather than every one: N=10 is ~40s of
        # audio, an acceptable worst-case loss for far less I/O.
        append_jsonl(partial, row, fsync=(since_flush >= cfg.checkpoint_every))
        if since_flush >= cfg.checkpoint_every:
            since_flush = 0

        if len(rows) % progress_every == 0:
            elapsed = time.time() - started
            done = segment.end - resume_from
            rtf = done / elapsed if elapsed > 0 else 0
            remaining = (duration - segment.end) / rtf if rtf > 0 else 0
            print(
                f"[transcribe] {segment.end:7.1f}s / {duration:.0f}s "
                f"({100 * segment.end / max(duration, 1):5.1f}%)  "
                f"RTF {rtf:.2f}x  ETA {remaining / 60:.0f}m",
                flush=True,
            )

    elapsed = time.time() - started
    payload = {
        "video_id": out_dir.name,
        "language": cfg.language,
        "duration": duration,
        "model": {
            "name": model_name,
            "requested": cfg.model,
            "compute_type": cfg.compute_type,
            "beam_size": cfg.beam_size,
            "vad": cfg.vad_filter,
            "condition_on_previous_text": False,
            "downgrade_reason": downgrade,
        },
        "timing": {
            "elapsed_s": round(elapsed, 1),
            "rtf": round(duration / elapsed, 3) if elapsed > 0 else None,
        },
        "segments": rows,
    }
    write_json(final, payload)
    print(
        f"[transcribe] done: {len(rows)} segments in {elapsed / 60:.1f} min "
        f"(RTF {payload['timing']['rtf']}x) -> {final}"
    )
    return payload


if __name__ == "__main__":
    import sys

    import yvc.bootstrap  # noqa: F401  (UTF-8 side effects)

    audio = sys.argv[1] if len(sys.argv) > 1 else "work/r39OrneyMDs/audio16k_raw.wav"
    out = sys.argv[2] if len(sys.argv) > 2 else "work/r39OrneyMDs"
    transcribe(audio, out)
