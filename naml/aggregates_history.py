"""Append-only per-day rollup used by the Settings → Health page.

Slice-12 owns the live in-memory aggregator. The Health page wants
30-day trend graphs (cost, tier-1, sprints/week, etc.) — those values are
*daily* and don't change inside a day at a meaningful rate. Rather than
re-replay every JSONL on every page load, the server periodically dumps
a daily rollup line to ``state/aggregates-history.jsonl``.

This module owns the file format and the read path. The write path is a
thin helper any aggregator-aware code can call; the HTTP endpoint reads
the file and returns the last N days.

JSONL line shape:
    {"day": "2026-05-20", "cost_usd": 4.21, "tokens": 1230456,
     "sprints_started": 1, "sprints_completed": 0,
     "tier1_hit_count": 3, "slice_fails": 0}

The schema is duck-typed; missing fields default to 0 / "".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_RETENTION_DAYS = 90


@dataclass(frozen=True)
class HistoryPoint:
    day: str  # yyyy-mm-dd UTC
    cost_usd: float = 0.0
    tokens: int = 0
    sprints_started: int = 0
    sprints_completed: int = 0
    tier1_hit_count: int = 0
    slice_fails: int = 0


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _parse_line(line: str) -> HistoryPoint | None:
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    day = raw.get("day")
    if not isinstance(day, str):
        return None
    # Quick validation — must look like yyyy-mm-dd
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    return HistoryPoint(
        day=day,
        cost_usd=float(raw.get("cost_usd", 0.0) or 0.0),
        tokens=int(raw.get("tokens", 0) or 0),
        sprints_started=int(raw.get("sprints_started", 0) or 0),
        sprints_completed=int(raw.get("sprints_completed", 0) or 0),
        tier1_hit_count=int(raw.get("tier1_hit_count", 0) or 0),
        slice_fails=int(raw.get("slice_fails", 0) or 0),
    )


def read_history(path: Path, *, days: int = 30) -> list[HistoryPoint]:
    """Return the last ``days`` daily rollups, oldest-first.

    Same-day duplicates collapse to the last-written entry (this is how
    the periodic dump rewrites today's row before midnight).
    """
    if not path.is_file():
        return []
    by_day: dict[str, HistoryPoint] = {}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in content.splitlines():
        point = _parse_line(line)
        if point is None:
            continue
        by_day[point.day] = point
    ordered = sorted(by_day.values(), key=lambda p: p.day)
    if days > 0:
        ordered = ordered[-days:]
    return ordered


def append_point(path: Path, point: HistoryPoint) -> None:
    """Append a JSONL line. Creates parents on first write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(point), separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def write_or_replace_today(path: Path, point: HistoryPoint) -> None:
    """Replace the rolling row for ``point.day`` (or append if absent).

    The periodic dump should call this so re-reads inside the same day
    don't double-count. Implementation: read every line, drop matching
    days, append the new point, atomic-rename back.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    kept: list[str] = []
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            content = ""
        for line in content.splitlines():
            existing = _parse_line(line)
            if existing is None or existing.day == point.day:
                continue
            kept.append(line)
    kept.append(json.dumps(asdict(point), separators=(",", ":")))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    tmp.replace(path)


def build_response(path: Path, *, days: int = 30) -> dict[str, object]:
    """Build the wire payload for ``GET /api/aggregates-history``."""
    points = read_history(path, days=days)
    return {
        "points": [asdict(p) for p in points],
    }


def derive_today_point(
    *,
    cost_usd: float,
    tokens: int,
    sprints_started: int = 0,
    sprints_completed: int = 0,
    tier1_hit_count: int = 0,
    slice_fails: int = 0,
    today: str | None = None,
) -> HistoryPoint:
    """Convenience constructor used by the periodic dump."""
    return HistoryPoint(
        day=today or _today_utc(),
        cost_usd=cost_usd,
        tokens=tokens,
        sprints_started=sprints_started,
        sprints_completed=sprints_completed,
        tier1_hit_count=tier1_hit_count,
        slice_fails=slice_fails,
    )


def iter_recent(path: Path, *, days: int = 30) -> Iterable[HistoryPoint]:
    """Iterator alias of :func:`read_history` for callers that prefer it."""
    return iter(read_history(path, days=days))
