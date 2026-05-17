"""Token-based usage tracker for the Claude plan windows.

There is no public API for "how much of my plan is used right now", so this
walks the local session transcripts under ``CLAUDE_CONFIG_DIR/projects/``
and sums tokens by timestamp. Same data source as the Claude Code TUI's
`/usage` view — the only difference is the LIMIT, which Anthropic does not
publish and which the user supplies via TOML (read off their own `/usage`
view once).

Three windows are tracked:

  session — rolling 5h     (the per-window cap that pages mid-conversation)
  weekly  — rolling 7d     (the multi-day cap)
  spend   — arbitrary span ("today" = 24h, "30 days" = 30d)

Each window returns:
  - token counts split by category (input, output, cache read, cache write)
  - cost in USD (computed from tokens × model price table)
  - reset_in_seconds (until oldest counted token ages out of the window)
  - by_model breakdown (so the UI can render rows per model)

Caveats:
  - Local-transcripts-only. Anything done on claude.ai web, on another
    machine, or under a different ``CLAUDE_CONFIG_DIR`` is invisible.
  - Prices are baked in from Anthropic's published rates as of late 2025.
    Override per-model via the TOML if rates change.
  - Plan caps are user-supplied. When unset, the UI shows raw tokens and
    cost without a percent bar.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import config


# --- pricing --------------------------------------------------------------

# USD per million tokens. Public Anthropic pricing snapshot for the current
# 4.x family. Pricing can change — override via [claude.prices.<model>] in
# .agents-orchestrator.toml when it does. Cache-write rates differ by TTL:
#   default ("5m"): 1.25× input rate
#   extended ("1h"): 2.00× input rate
# Cache-read is 0.10× input rate across the board.
_BASE_PRICES: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.0, "output": 75.0,
        "cache_read": 1.5, "cache_write_5m": 18.75, "cache_write_1h": 30.0,
    },
    "claude-sonnet-4-6": {
        "input": 3.0, "output": 15.0,
        "cache_read": 0.30, "cache_write_5m": 3.75, "cache_write_1h": 6.0,
    },
    "claude-haiku-4-5": {
        "input": 1.0, "output": 5.0,
        "cache_read": 0.10, "cache_write_5m": 1.25, "cache_write_1h": 2.0,
    },
}


def _price_table(model: str) -> dict[str, float]:
    """Return the per-MTok rate table for a model. Falls back to Opus rates
    (the most expensive) for unknown model strings, so cost is never
    silently zero — better to over-estimate than to hide spend."""
    if not isinstance(model, str):
        return _BASE_PRICES["claude-opus-4-7"]
    if model in _BASE_PRICES:
        return _BASE_PRICES[model]
    # Fuzzy match by family name in the model id.
    low = model.lower()
    if "opus" in low:
        return _BASE_PRICES["claude-opus-4-7"]
    if "sonnet" in low:
        return _BASE_PRICES["claude-sonnet-4-6"]
    if "haiku" in low:
        return _BASE_PRICES["claude-haiku-4-5"]
    return _BASE_PRICES["claude-opus-4-7"]


# --- shapes ---------------------------------------------------------------

@dataclass(frozen=True)
class Usage:
    """Token + cost rollup for a window."""
    window_seconds: int
    ok: bool = True                             # False when no transcripts found
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cost_usd: float = 0.0
    oldest_age_seconds: int | None = None       # for reset countdown
    by_model: dict[str, dict] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read_tokens
                + self.cache_write_5m_tokens + self.cache_write_1h_tokens)

    @property
    def reset_in_seconds(self) -> int | None:
        if self.oldest_age_seconds is None:
            return None
        return max(0, self.window_seconds - self.oldest_age_seconds)


# Back-compat alias for orchestrator code that still imports Estimate.
Estimate = Usage


# --- transcript walking ---------------------------------------------------

def _parse_ts(raw: str | None) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iter_assistant_usage(projects_dir: Path) -> Iterable[tuple[float, str, dict]]:
    """Yield (epoch_seconds, model, usage_dict) for every assistant turn
    found in any transcript under ``projects_dir``. Lazy — does not load
    the whole tree into memory."""
    if not projects_dir.is_dir():
        return
    for path in projects_dir.glob("*/*.jsonl"):
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    ts = _parse_ts(d.get("timestamp"))
                    if ts is None:
                        continue
                    msg = d.get("message") or {}
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    model = msg.get("model") or "unknown"
                    yield ts, model, usage
        except OSError:
            continue


def _cost_for(model: str, *, inp: int, out: int,
              cache_read: int, cw_5m: int, cw_1h: int) -> float:
    p = _price_table(model)
    return (
        inp * p["input"]
        + out * p["output"]
        + cache_read * p["cache_read"]
        + cw_5m * p["cache_write_5m"]
        + cw_1h * p["cache_write_1h"]
    ) / 1_000_000.0


def _empty_model_row() -> dict:
    return {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0, "cache_write_1h_tokens": 0,
        "cost_usd": 0.0,
    }


def _split_cache_writes(usage: dict) -> tuple[int, int]:
    """Pull (5m, 1h) cache-write token counts from a usage block.

    Newer transcripts carry a ``cache_creation`` sub-block with the split;
    older ones only have the aggregate ``cache_creation_input_tokens``.
    For aggregates with no split, attribute everything to 5m (the default
    TTL) — under-counting 1h-cache cost is the conservative bias."""
    split = usage.get("cache_creation") or {}
    if isinstance(split, dict):
        five_m = int(split.get("ephemeral_5m_input_tokens", 0) or 0)
        one_h = int(split.get("ephemeral_1h_input_tokens", 0) or 0)
        if five_m or one_h:
            return five_m, one_h
    agg = int(usage.get("cache_creation_input_tokens", 0) or 0)
    return agg, 0


def _window(seconds: int, now: float | None = None) -> Usage:
    """Build a Usage rollup for the last ``seconds`` of transcripts."""
    cfg = config.load()
    now = now or time.time()
    cutoff = now - seconds

    projects = cfg.claude_config_dir / "projects"
    if not projects.is_dir():
        return Usage(window_seconds=seconds, ok=False)

    inp = out = cache_r = cw_5m = cw_1h = 0
    cost = 0.0
    oldest_ts: float | None = None
    by_model: dict[str, dict] = {}
    seen_any = False

    for ts, model, usage in _iter_assistant_usage(projects):
        seen_any = True
        if ts < cutoff:
            continue
        i = int(usage.get("input_tokens", 0) or 0)
        o = int(usage.get("output_tokens", 0) or 0)
        cr = int(usage.get("cache_read_input_tokens", 0) or 0)
        c5, c1 = _split_cache_writes(usage)

        inp += i; out += o; cache_r += cr
        cw_5m += c5; cw_1h += c1
        cost += _cost_for(model, inp=i, out=o, cache_read=cr, cw_5m=c5, cw_1h=c1)

        row = by_model.setdefault(model, _empty_model_row())
        row["input_tokens"] += i
        row["output_tokens"] += o
        row["cache_read_tokens"] += cr
        row["cache_write_5m_tokens"] += c5
        row["cache_write_1h_tokens"] += c1
        row["cost_usd"] += _cost_for(model, inp=i, out=o, cache_read=cr, cw_5m=c5, cw_1h=c1)

        if oldest_ts is None or ts < oldest_ts:
            oldest_ts = ts

    if not seen_any:
        return Usage(window_seconds=seconds, ok=False)

    age = int(now - oldest_ts) if oldest_ts is not None else None
    # Round per-model costs once at the end.
    for row in by_model.values():
        row["cost_usd"] = round(row["cost_usd"], 4)

    return Usage(
        window_seconds=seconds, ok=True,
        input_tokens=inp, output_tokens=out, cache_read_tokens=cache_r,
        cache_write_5m_tokens=cw_5m, cache_write_1h_tokens=cw_1h,
        cost_usd=round(cost, 4), oldest_age_seconds=age, by_model=by_model,
    )


# --- public entry points --------------------------------------------------

_SESSION_SECONDS = 5 * 60 * 60
_WEEK_SECONDS = 7 * 24 * 60 * 60
_DAY_SECONDS = 24 * 60 * 60
_THIRTY_DAYS = 30 * 24 * 60 * 60


def session_usage() -> Usage:
    """Rolling 5-hour window."""
    return _window(_SESSION_SECONDS)


def weekly_usage() -> Usage:
    """Rolling 7-day window."""
    return _window(_WEEK_SECONDS)


def today_usage() -> Usage:
    """Rolling 24-hour window — used for the 'cost today' rollup."""
    return _window(_DAY_SECONDS)


def thirty_day_usage() -> Usage:
    """Rolling 30-day window — used for the 'last 30 days' rollup."""
    return _window(_THIRTY_DAYS)


# Back-compat: orchestrator/status used to call ``estimate()`` and read
# ``.used / .limit / .headroom``. Keep that working by mapping it onto the
# new session_usage() + the configured session_token_limit.

def estimate() -> Usage:
    return session_usage()


# --- helpers exposed to status / orchestrator -----------------------------

def session_headroom_tokens() -> int | None:
    """Tokens remaining in the session window. None when no cap configured."""
    cfg = config.load()
    limit = getattr(cfg, "session_token_limit", None)
    if not limit:
        return None
    used = session_usage().total_tokens
    return max(0, limit - used)


def weekly_headroom_tokens() -> int | None:
    cfg = config.load()
    limit = getattr(cfg, "weekly_token_limit", None)
    if not limit:
        return None
    used = weekly_usage().total_tokens
    return max(0, limit - used)


# --- calibration ---------------------------------------------------------

def back_solve_cap(used_tokens: int, pct_used: float,
                   *, round_to_nearest: int = 1_000_000) -> int | None:
    """Given current tokens used + the % reading from Claude's /usage view,
    return the implied plan cap. Rounded up to a clean increment so the
    suggested TOML value isn't a weird specific number.

    Returns None for nonsense inputs (pct ≤ 0 or > 100, tokens 0)."""
    if not isinstance(pct_used, (int, float)) or pct_used <= 0 or pct_used > 100:
        return None
    if used_tokens <= 0:
        return None
    raw = used_tokens / (pct_used / 100.0)
    if round_to_nearest <= 0:
        return int(raw)
    # Round UP so we don't accidentally configure a cap below current usage.
    chunks = (raw + round_to_nearest - 1) // round_to_nearest
    return int(chunks * round_to_nearest)


def calibrate(session_pct: float | None = None,
              weekly_pct: float | None = None) -> dict:
    """Build a calibration report: current tokens + the implied caps.

    Use the result to update ``[claude] session_token_limit`` /
    ``weekly_token_limit`` in the TOML.
    """
    s = session_usage()
    w = weekly_usage()
    return {
        "session": {
            "used_tokens": s.total_tokens,
            "ok": s.ok,
            "pct_used": session_pct,
            "suggested_cap": back_solve_cap(s.total_tokens, session_pct) if session_pct else None,
        },
        "weekly": {
            "used_tokens": w.total_tokens,
            "ok": w.ok,
            "pct_used": weekly_pct,
            "suggested_cap": back_solve_cap(w.total_tokens, weekly_pct) if weekly_pct else None,
        },
    }
