"""Best-effort estimator for the Claude Pro 5-hour rolling message window.

There is no public API for "messages used this window", so this walks the
local Claude session transcripts (under ``CLAUDE_CONFIG_DIR/projects/``) and
counts genuine *user prompts* (not assistant steps, not tool results) with a
timestamp inside the last 5 hours. One ``claude -p`` invocation logs roughly
one such prompt, which is the closest local proxy for what the plan meters.

It is deliberately approximate. The wall-clock run cap and slow mode's
one-issue-per-invocation ceiling are the real safety net; this estimator
only adds a courtesy guard.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import NamedTuple

from . import config


class Estimate(NamedTuple):
    used: int | None          # messages counted in the window; None if unknown
    limit: int                # plan cap
    reset_in_seconds: int | None  # until the oldest counted message ages out
    ok: bool                  # False when discovery failed

    @property
    def headroom(self) -> int | None:
        if self.used is None:
            return None
        return max(0, self.limit - self.used)


def _parse_ts(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'."""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _is_user_prompt(entry: dict) -> bool:
    """True if a transcript entry is a genuine user prompt.

    Claude Code logs tool results as ``type: "user"`` too — those carry
    ``tool_result`` content blocks and must not be counted. A real prompt is a
    plain string, or a content array with at least one text block.
    """
    if entry.get("type") != "user":
        return False
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and block.get("type") == "text"
            for block in content
        )
    return False


def estimate(now: datetime | None = None) -> Estimate:
    """Count user prompts within the rolling Pro window.

    Returns ``ok=False`` with ``used=None`` if no session transcripts could be
    read — callers decide how conservative to be with an unknown.
    """
    cfg = config.load()
    now = now or datetime.now(timezone.utc)
    window_start = now.timestamp() - cfg.pro_window_seconds

    projects_dir = cfg.claude_config_dir / "projects"
    if not projects_dir.is_dir():
        return Estimate(used=None, limit=cfg.pro_limit, reset_in_seconds=None, ok=False)

    transcripts = list(projects_dir.glob("*/*.jsonl"))
    if not transcripts:
        return Estimate(used=None, limit=cfg.pro_limit, reset_in_seconds=None, ok=False)

    timestamps: list[float] = []
    read_any = False
    for path in transcripts:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    read_any = True
                    if not _is_user_prompt(entry):
                        continue
                    ts = _parse_ts(entry.get("timestamp", ""))
                    if ts is None:
                        continue
                    epoch = ts.timestamp()
                    if epoch >= window_start:
                        timestamps.append(epoch)
        except OSError:
            continue

    if not read_any:
        return Estimate(used=None, limit=cfg.pro_limit, reset_in_seconds=None, ok=False)

    used = len(timestamps)
    if timestamps:
        oldest = min(timestamps)
        reset_in = int(oldest + cfg.pro_window_seconds - now.timestamp())
        reset_in = max(0, reset_in)
    else:
        reset_in = 0
    return Estimate(used=used, limit=cfg.pro_limit, reset_in_seconds=reset_in, ok=True)
