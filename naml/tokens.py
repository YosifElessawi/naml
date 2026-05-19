"""Per-turn token + cost emitter for the cockpit's live telemetry pipeline.

Claude Code's ``--output-format stream-json`` emits one JSON object per line.
The objects we care about for telemetry are ``type:"assistant"`` events: each
one carries a ``usage`` block with the current turn's token counts. We extract
those, compute a context-window % against the configured model max, and append
one line to ``state/<slice>.tokens.jsonl`` — the source of truth for the
cockpit's cost timeline + context-window meters.

Defensive design:
- A malformed JSON line is logged to stderr and skipped (does NOT crash the
  lane). Claude's output format evolves; a future field must not break us.
- ``usage`` may live at the top level OR nested under ``message`` depending
  on the Claude version. Both are handled.
- Atomic per-line appends: each turn is written as one ``write(line + "\\n")``
  call against an ``O_APPEND`` fd. POSIX guarantees writes ≤ PIPE_BUF bytes
  are atomic, which means concurrent emitters never interleave.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


log = logging.getLogger("naml.tokens")


# Claude Code per-turn cost is NOT emitted in the stream as of v2 stream-json.
# The final ``type:"result"`` event has the cumulative ``total_cost_usd``, but
# live telemetry needs a per-turn value. We compute it from per-turn token
# counts against a small static price table keyed by model id.
#
# Prices are USD per million tokens. Values track Anthropic's public price
# card and should be updated when prices change. Cache-read is roughly 10%
# of input; cache-write (5-minute ephemeral) is roughly 125% of input.
# Unknown model → cost_usd = 0.0 (still useful for tokens / ctx %).
_PRICE_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-4-7":     {"in": 15.0, "out": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-opus-4-6":     {"in": 15.0, "out": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-opus-4-5":     {"in": 15.0, "out": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-sonnet-4-6":   {"in":  3.0, "out": 15.0, "cache_read": 0.30, "cache_write":  3.75},
    "claude-sonnet-4-5":   {"in":  3.0, "out": 15.0, "cache_read": 0.30, "cache_write":  3.75},
    "claude-haiku-4-5":    {"in":  1.0, "out":  5.0, "cache_read": 0.10, "cache_write":  1.25},
}


def _normalise_model(model: str) -> str:
    """Strip vendor-specific suffixes Claude Code appends, e.g. ``[1m]``."""
    return model.split("[", 1)[0].strip() if model else ""


@dataclass(frozen=True)
class TurnUsage:
    """The slice of a usage block we persist per turn."""

    tokens_in: int
    tokens_out: int
    cache_read: int
    cache_write: int

    @property
    def total_input_eq(self) -> int:
        """Effective input tokens that count toward the context window.

        Cache-read tokens still occupy the model's context, so they count.
        Cache-write tokens are part of the input on the turn they're written,
        and are accounted for in ``tokens_in`` already on most SDKs.
        """
        return self.tokens_in + self.cache_read


def parse_usage(usage: Any) -> TurnUsage | None:
    """Pull our four counters out of a Claude ``usage`` dict. Defensive."""
    if not isinstance(usage, dict):
        return None
    try:
        tokens_in = int(usage.get("input_tokens", 0) or 0)
        tokens_out = int(usage.get("output_tokens", 0) or 0)
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
    except (TypeError, ValueError):
        return None
    return TurnUsage(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        cache_write=cache_write,
    )


def _extract_usage(event: dict) -> TurnUsage | None:
    """Pull a ``TurnUsage`` from an assistant event, regardless of nesting.

    Claude Code wraps the assistant message under ``message`` in stream-json:
    ``{"type": "assistant", "message": {..., "usage": {...}}}``. Some older
    versions / SDK shapes put ``usage`` at the top level. We try both.
    """
    if event.get("type") != "assistant":
        return None
    inner = event.get("message") if isinstance(event.get("message"), dict) else None
    if inner is not None:
        u = parse_usage(inner.get("usage"))
        if u is not None:
            return u
    return parse_usage(event.get("usage"))


def compute_ctx_pct(usage: TurnUsage, model_context_max: int) -> int:
    """Return integer percent of model context occupied this turn.

    Formula matches ``artifacts/spec.md`` Q8b: ``(tokens_in + cache_read) /
    model_context_max``. Clamped to [0, 100] so a transient spike or a
    misconfigured ``model_context_max`` can't produce a 9000% bar.
    """
    if model_context_max <= 0:
        return 0
    pct = (usage.total_input_eq / model_context_max) * 100.0
    if pct < 0:
        return 0
    if pct > 100:
        return 100
    return int(round(pct))


def compute_cost_usd(usage: TurnUsage, model: str) -> float:
    """Per-turn USD cost from token counts + a static model price table.

    Returns 0.0 for unknown models. Callers should not rely on this matching
    Anthropic's billing to the cent — the final ``type:"result"`` event
    carries an authoritative cumulative ``total_cost_usd`` that the lane
    records separately.
    """
    prices = _PRICE_PER_MTOK.get(_normalise_model(model))
    if prices is None:
        return 0.0
    cost = (
        usage.tokens_in    * prices["in"]
        + usage.tokens_out * prices["out"]
        + usage.cache_read * prices["cache_read"]
        + usage.cache_write * prices["cache_write"]
    ) / 1_000_000.0
    # Clamp to two-significant-decimal cents-equivalent precision so JSONL
    # diffs stay small. Real billing precision is 4 decimal places.
    return round(cost, 6)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _atomic_append(path: Path, line: str) -> None:
    """Append one fully-formed line + ``\\n`` to ``path``.

    Opens with ``O_APPEND | O_CREAT | O_WRONLY`` and writes once. On POSIX
    this is atomic for writes ≤ PIPE_BUF bytes (4096 on Linux, 512 on macOS
    by POSIX guarantee — but our JSONL lines are ≤ a few hundred bytes), so
    multiple concurrent emitters can never interleave a partial line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not line.endswith("\n"):
        line = line + "\n"
    data = line.encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


class TurnEmitter:
    """Stateful per-slice emitter for stream-json turns.

    Hand it the JSONL path + slice context once; feed each Claude stream-json
    line via ``feed_line``. The emitter parses, ignores non-usage events,
    and appends one record per assistant turn.

    Thread-safe: ``feed_line`` is guarded by a lock so the implementer reader
    thread + ad-hoc test calls can't race on the turn counter.
    """

    def __init__(
        self,
        *,
        jsonl_path: Path,
        slice_id: str,
        session_id: str,
        model_context_max: int,
        model: str = "",
        start_turn: int = 0,
    ) -> None:
        self._path = jsonl_path
        self._slice_id = slice_id
        self._session_id = session_id
        self._model = model  # may be overwritten on the first system:init event
        self._model_context_max = model_context_max
        self._turn = start_turn
        self._lock = threading.Lock()

    @property
    def turns_emitted(self) -> int:
        return self._turn

    def feed_line(self, raw: str) -> bool:
        """Parse one stream-json line. Returns True if an emit occurred.

        Garbage / non-JSON / unrelated events return False without raising.
        """
        line = raw.strip()
        if not line or not line.startswith("{"):
            return False
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # Defensive: a future Claude version might emit a weird line.
            # Log to stderr (the lane's log capture will show it), don't kill
            # the run.
            print(
                f"[naml.tokens] skipping malformed stream-json line: {exc!s}",
                file=sys.stderr,
            )
            return False
        if not isinstance(ev, dict):
            return False
        return self.feed_event(ev)

    def feed_event(self, ev: dict) -> bool:
        """Process a parsed event dict. Returns True if a JSONL line was written."""
        # Pick up model from the init event so cost_usd has the right rate
        # card before the first assistant turn arrives.
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            model = ev.get("model")
            if isinstance(model, str) and model:
                self._model = model
            return False

        usage = _extract_usage(ev)
        if usage is None:
            return False
        try:
            self._emit(usage)
        except OSError as exc:
            # Disk full / permissions / etc — log and keep going. Losing a
            # telemetry line is far better than crashing the lane mid-turn.
            print(
                f"[naml.tokens] could not append turn to {self._path}: {exc!s}",
                file=sys.stderr,
            )
            if exc.errno == errno.ENOSPC:
                raise
            return False
        return True

    def _emit(self, usage: TurnUsage) -> None:
        with self._lock:
            self._turn += 1
            record = {
                "t": _now_iso(),
                "slice": self._slice_id,
                "session": self._session_id,
                "turn": self._turn,
                "tokens_in": usage.tokens_in,
                "tokens_out": usage.tokens_out,
                "cache_read": usage.cache_read,
                "cache_write": usage.cache_write,
                "cost_usd": compute_cost_usd(usage, self._model),
                "ctx_pct": compute_ctx_pct(usage, self._model_context_max),
            }
            _atomic_append(self._path, json.dumps(record, separators=(",", ":")))
