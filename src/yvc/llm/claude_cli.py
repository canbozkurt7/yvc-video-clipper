"""Headless Claude CLI driver used as the pipeline's LLM engine.

There is no Anthropic API key on this machine, but the `claude` CLI is
authenticated, so it is invoked as a subprocess. That choice brings a set
of quirks which were measured rather than assumed:

  * The CLI wraps its answer in a JSON envelope; the model's text sits in
    the ``result`` field.
  * The model wraps JSON in a ```json fence even when told not to.
  * `claude` on Windows is an npm ``.cmd`` shim, so it cannot be executed
    directly with ``shell=False`` -- it has to go through COMSPEC.
  * A trivial call costs roughly 27.8k cache-creation tokens because the
    CLI injects its full system prompt on every invocation. Caching
    responses on disk is therefore both a correctness feature (identical
    reruns) and the main cost lever.

Nothing here assumes the model is well behaved. Every response goes
through a repair ladder and schema validation before a caller sees it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from yvc.bootstrap import child_env
from yvc.io import append_jsonl, read_json, write_json

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")
_SMART_QUOTES = str.maketrans(
    {"“": '"', "”": '"', "‘": "'", "’": "'"}
)


class LLMError(RuntimeError):
    """Base class. ``transient`` decides whether a retry could help."""

    transient = False


class LLMTransientError(LLMError):
    transient = True


class LLMSchemaError(LLMError):
    """The model could not produce output matching the schema."""


@dataclass
class LLMResult:
    data: Any
    raw: str
    attempts: int
    duration_s: float
    repaired: bool
    cache_hit: bool


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate a process and every descendant.

    Killing the immediate child is not enough when it is a shell wrapping
    the real program: the grandchild survives, holds the inherited pipe
    handles, and blocks the parent indefinitely.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=30,
            )
            return
        except Exception:
            pass  # fall through to the best-effort kill below
    try:
        proc.kill()
    except Exception:
        pass


@dataclass
class ClaudeCLI:
    """Thin, defensive wrapper around ``claude -p``."""

    exe: str | None = None
    cache_dir: Path | None = None
    log_path: Path | None = None
    # Measured on this machine: a copywriting call runs ~122 s end to end,
    # of which ~99 s is time-to-first-token -- queueing, not generation. A
    # 300 s ceiling left barely two standard deviations of headroom, so a
    # busy period turned into a retry storm at two minutes a throw.
    timeout_s: int = 600
    max_attempts: int = 3
    # How many `claude` sessions the LLM stages may keep in flight. Not
    # used by complete() itself -- each call is independent and already
    # thread-safe -- it is the pool width the stages size themselves from.
    # 1 keeps every stage strictly serial, as it was before.
    concurrency: int = 1
    _resolved: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        exe = self.exe or shutil.which("claude") or "claude"
        # npm installs `claude` as a .cmd shim on Windows. subprocess with
        # shell=False cannot execute a .cmd directly, so route through the
        # command interpreter. Doing this explicitly beats shell=True,
        # which would reintroduce quoting problems with Turkish prompts.
        if exe.lower().endswith((".cmd", ".bat")):
            self._resolved = [os.environ.get("COMSPEC", "cmd.exe"), "/c", exe]
        else:
            self._resolved = [exe]

    @classmethod
    def from_config(cls, llm_cfg: dict | None = None) -> "ClaudeCLI":
        """Build from the ``llm:`` section of config.yaml.

        Without this the section was decorative: every stage constructed
        ``ClaudeCLI()`` with hard-coded defaults, so editing `timeout_s`
        or `cache` in config changed nothing. Config that looks live but
        is not is worse than no config at all.
        """
        cfg = llm_cfg or {}
        cache_dir = None
        if cfg.get("cache", True):
            cache_dir = Path(cfg.get("cache_dir", ".yvc/llm_cache"))
        return cls(
            timeout_s=int(cfg.get("timeout_s", 600)),
            max_attempts=int(cfg.get("max_attempts", 3)),
            concurrency=max(1, int(cfg.get("concurrency", 1))),
            cache_dir=cache_dir,
            log_path=Path(cfg["log_path"]) if cfg.get("log_path") else None,
        )

    # ---------------------------------------------------------------- cache

    def _cache_key(self, task: str, prompt: str, schema_name: str) -> str:
        digest = hashlib.sha256(
            f"{task}\x00{schema_name}\x00{prompt}".encode("utf-8")
        ).hexdigest()
        return digest[:32]

    def _cache_read(self, key: str) -> Any | None:
        if self.cache_dir is None:
            return None
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            return read_json(path)
        except Exception:
            return None

    def _cache_write(self, key: str, payload: Any) -> None:
        if self.cache_dir is None:
            return
        write_json(self.cache_dir / f"{key}.json", payload)

    # --------------------------------------------------------------- invoke

    def _invoke(self, prompt: str, model: str | None) -> str:
        cmd = [*self._resolved, "-p", "--output-format", "json", "--max-turns", "1"]
        if model:
            cmd += ["--model", model]
        # Strip tool access: these are pure text-in/JSON-out calls, and the
        # tool definitions are a large share of the per-call token cost.
        #
        # `--tools ""` and not `--allowed-tools ""`. The latter is a
        # permission filter: the definitions are still sent, so the model
        # still reaches for a tool, the call is denied, and with
        # --max-turns 1 the turn ends on stop_reason=tool_use having
        # produced no text. The CLI then exits 1 with an empty stderr,
        # which surfaces as an unexplained failure. Measured on the six
        # clips of r39OrneyMDs: --allowed-tools 0/6, --tools 6/6, with
        # cache_read_input_tokens falling from ~23k to ~2.3k because the
        # definitions genuinely stop being sent. Raising --max-turns is
        # not the fix -- it gives the model more turns to keep trying
        # tools (12 denials over 4 turns in the same test).
        #
        # The trigger is the character budget in the copywriting prompt:
        # asked to hit "max N karakter", the model writes a script to
        # count for it.
        cmd += ["--tools", ""]

        # NOT subprocess.run(timeout=...). On Windows the command runs as
        # cmd.exe -> claude, and run()'s timeout kills only cmd.exe. The
        # orphaned grandchild keeps the stdout/stderr pipes open, so the
        # post-kill communicate() blocks forever waiting for a writer that
        # never closes -- the timeout silently becomes an infinite hang.
        # Observed in practice: a copywriting call sat for 18+ minutes on a
        # 300 s timeout while the pipeline made no progress.
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,     # prompt via stdin, never argv: Turkish
            stdout=subprocess.PIPE,    # text, PowerShell quoting, 32k argv cap
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env(),
        )
        try:
            stdout, stderr_text = proc.communicate(prompt, timeout=self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            _kill_tree(proc)
            try:
                # Now that the whole tree is gone the pipes can drain.
                proc.communicate(timeout=15)
            except Exception:
                pass
            raise LLMTransientError(
                f"claude timed out after {self.timeout_s}s"
            ) from exc

        if proc.returncode != 0:
            stderr_text = (stderr_text or "").strip()
            if _looks_like_usage_limit(stderr_text):
                raise LLMTransientError(f"usage limit reached: {stderr_text[:200]}")
            raise LLMTransientError(
                f"claude exited {proc.returncode}: "
                f"{stderr_text[:300] or _envelope_hint(stdout)}"
            )

        return stdout

    @staticmethod
    def _unwrap_envelope(stdout: str) -> str:
        """Pull the model's text out of the CLI's JSON envelope."""
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout  # Already bare text.
        if isinstance(envelope, dict):
            if envelope.get("is_error"):
                raise LLMTransientError(f"CLI reported error: {envelope.get('result')}")
            for key in ("result", "content", "text"):
                value = envelope.get(key)
                if isinstance(value, str):
                    return value
        return stdout

    # ---------------------------------------------------------------- parse

    @staticmethod
    def _extract_json(text: str) -> Any:
        """Repair ladder. Each step handles a failure mode seen in practice."""
        candidate = _FENCE.sub("", text.strip())

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Prose preamble before the JSON: take the outermost balanced block.
        block = _balanced_block(candidate)
        if block is not None:
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                candidate = block

        tolerant = candidate.translate(_SMART_QUOTES)
        tolerant = re.sub(r",\s*([}\]])", r"\1", tolerant)  # trailing commas
        tolerant = re.sub(r"\bNaN\b|\bNone\b", "null", tolerant)
        try:
            return json.loads(tolerant)
        except json.JSONDecodeError:
            pass

        # Unescaped double quotes inside string values. This task asks for a
        # *verbatim quotation* in `evidence_quote`, so the model is being
        # invited to write the one character that breaks JSON. Measured: two
        # consecutive ~600 s calls lost to it, which is the whole stage.
        try:
            return json.loads(_escape_inner_quotes(tolerant))
        except json.JSONDecodeError as exc:
            raise LLMSchemaError(f"unparseable JSON: {exc}") from exc

    # ----------------------------------------------------------------- main

    def complete(
        self,
        task: str,
        prompt: str,
        schema: type[T],
        *,
        model: str | None = None,
        use_cache: bool = True,
    ) -> LLMResult:
        """Run one prompt and return a validated instance of ``schema``."""
        schema_name = schema.__name__
        key = self._cache_key(task, prompt, schema_name)

        if use_cache:
            cached = self._cache_read(key)
            if cached is not None:
                return LLMResult(
                    data=schema.model_validate(cached),
                    raw="<cache>",
                    attempts=0,
                    duration_s=0.0,
                    repaired=False,
                    cache_hit=True,
                )

        instruction = _with_schema(prompt, schema)

        started = time.time()
        last_error: Exception | None = None
        repaired = False
        raw = ""

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self._invoke(instruction, model)
                text = self._unwrap_envelope(raw)
                parsed = self._extract_json(text)
                data = schema.model_validate(parsed)
            except LLMTransientError as exc:
                last_error = exc
                self._log(task, attempt, "transient", str(exc))
                time.sleep(min(2.0**attempt, 30.0))
                continue
            except (LLMSchemaError, ValidationError) as exc:
                last_error = exc
                self._log(task, attempt, "schema", str(exc)[:400])
                # Feed the failure back and ask again. This recovers most
                # near-miss responses without retrying from scratch.
                instruction = _with_repair(prompt, schema, str(exc)[:600])
                repaired = True
                continue

            duration = time.time() - started
            if use_cache:
                self._cache_write(key, data.model_dump(mode="json"))
            self._log(task, attempt, "ok", None, duration)
            return LLMResult(data, raw, attempt, duration, repaired, False)

        raise LLMSchemaError(
            f"{task}: failed after {self.max_attempts} attempts: {last_error}"
        )

    # ------------------------------------------------------------------ log

    def _log(
        self,
        task: str,
        attempt: int,
        status: str,
        error: str | None,
        duration: float | None = None,
    ) -> None:
        if self.log_path is None:
            return
        append_jsonl(
            self.log_path,
            {
                "task": task,
                "attempt": attempt,
                "status": status,
                "error": error,
                "duration_s": round(duration, 2) if duration else None,
            },
        )

    def health(self) -> dict[str, Any]:
        """Probe used by ``yvc doctor`` to confirm the CLI is usable."""
        try:
            proc = subprocess.run(
                [*self._resolved, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env(),
                timeout=60,
            )
            return {
                "ok": proc.returncode == 0,
                "version": (proc.stdout or "").strip(),
                "invocation": self._resolved,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "invocation": self._resolved}


def _escape_inner_quotes(text: str) -> str:
    """Escape double quotes appearing *inside* JSON string values.

    A quote legitimately ends a string only when the next meaningful
    character is structural (``,`` ``:`` ``}`` ``]``) or the end of input.
    Anything else means the model wrote a quotation mark inside its prose,
    so the quote is escaped rather than treated as a terminator.

    Deliberately a scanner rather than a regex: the decision needs
    lookahead past whitespace plus knowledge of whether we are currently
    inside a string, and no honest regex expresses that.
    """
    out: list[str] = []
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if not in_string:
            out.append(char)
            if char == '"':
                in_string = True
            continue

        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            out.append(char)
            escaped = True
            continue

        if char == '"':
            if text[index + 1:].lstrip()[:1] in {",", ":", "}", "]", ""}:
                out.append(char)        # a genuine terminator
                in_string = False
            else:
                out.append('\\"')       # prose quote: escape it
            continue

        out.append(char)

    return "".join(out)


def _schema_text(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), ensure_ascii=False)


def _with_schema(prompt: str, schema: type[BaseModel]) -> str:
    return (
        f"{prompt}\n\n"
        "Yanitini YALNIZCA asagidaki JSON semasina uyan gecerli JSON olarak ver. "
        "Kod blogu isareti, aciklama veya onsoz ekleme.\n"
        # Cheaper to prevent than to repair: one stray quote costs a full
        # round trip, and this task explicitly asks for a verbatim quotation.
        "Metin degerlerinin ICINDE duz cift tirnak kullanma; alinti "
        "gerekiyorsa « » veya tek tirnak kullan.\n"
        f"SEMA:\n{_schema_text(schema)}"
    )


def _with_repair(prompt: str, schema: type[BaseModel], error: str) -> str:
    return (
        f"{prompt}\n\n"
        "Onceki yanitin sema dogrulamasindan gecmedi.\n"
        f"HATA: {error}\n"
        "Yalnizca semaya uyan gecerli JSON dondur.\n"
        f"SEMA:\n{_schema_text(schema)}"
    )


def _balanced_block(text: str) -> str | None:
    """Return the outermost balanced {...} or [...] block, ignoring strings."""
    start = None
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            break
    if start is None:
        return None

    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _envelope_hint(stdout: str) -> str:
    """Why the CLI exited non-zero, when stderr says nothing.

    A failed run still prints its JSON envelope on stdout, and that is
    where the reason actually lives: a turn that ended on a tool call
    exits 1 with an empty stderr, so the bare "claude exited 1:" that
    used to reach the caller named neither the stop reason nor the tool.
    """
    try:
        envelope = json.loads(stdout or "")
    except (json.JSONDecodeError, TypeError):
        return "no stderr, no parseable envelope"
    if not isinstance(envelope, dict):
        return "no stderr, no parseable envelope"
    stop = envelope.get("stop_reason")
    denied = [
        d.get("tool_name")
        for d in envelope.get("permission_denials") or []
        if isinstance(d, dict)
    ]
    hint = f"no stderr; stop_reason={stop!r}"
    if denied:
        hint += f", denied tool calls: {', '.join(filter(None, denied))}"
    if stop == "tool_use":
        hint += (
            " -- the model tried to call a tool and the turn ended there; "
            "the tool set should be empty (--tools \"\")"
        )
    return hint


def _looks_like_usage_limit(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in ("usage limit", "rate limit", "quota", "too many requests")
    )
