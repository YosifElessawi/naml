"""In-memory rollups of the per-slice token JSONL stream.

The cockpit's headline numbers (today $ · this week · 30d · lifetime) are
derived from one JSONL line per Claude turn, written by
:mod:`naml.tokens`. This module owns the running aggregates: per slice,
per sprint, and per project (with daily UTC buckets so the time-window
totals can be recomputed cheaply on demand).

Design choices the spec pins down (``artifacts/spec.md`` Q8b):

- **No DB.** All state is in RAM. Cold start rebuilds from JSONL.
- **O(1) per event.** ``apply_event`` is a fixed handful of dict
  lookups + adds. Time-window rollups are computed at ``snapshot`` time
  from a bounded set of daily buckets (≤ ~30 buckets matter for any
  window we expose).
- **UTC everywhere.** A "day" is a UTC calendar day. Local-time
  presentation lives in the browser. This dodges DST entirely.
- **Lifetime origin.** The aggregator records the timestamp of the
  first event it sees (or replays) and exposes it on the snapshot —
  the cockpit shows "Lifetime · since 2025-11-04" on the right rail.

Thread-safe: ``apply_event`` and ``snapshot`` hold an internal lock so
the watcher thread can append events while the aiohttp request loop
reads the snapshot for ``GET /aggregates``.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any


# Number of days a "this_week" window covers, including today.
WEEK_DAYS = 7
# Number of days a "last_30d" window covers, including today.
MONTH_DAYS = 30


def _parse_event_time(value: Any) -> datetime | None:
    """Parse the ``t`` field on a token event. ``None`` if unparseable.

    Token events written by :mod:`naml.tokens` use ``datetime.isoformat()``
    on a UTC-aware ``datetime``, so they always carry an offset. We accept
    naive strings too (treat them as UTC) — robust against any one-off
    fixture that drops the offset.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date_key(dt: datetime) -> str:
    """UTC ``YYYY-MM-DD`` bucket key for an aware datetime."""
    return dt.astimezone(timezone.utc).date().isoformat()


@dataclass
class WindowTotals:
    """One bucket of rolled-up token counters. Used for daily buckets and
    for the today / week / 30d / lifetime fields on a snapshot."""

    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    turns: int = 0

    def add(self, other: "WindowTotals") -> None:
        self.cost_usd += other.cost_usd
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        self.turns += other.turns

    def to_dict(self) -> dict[str, Any]:
        # Round cost so the JSON payload stays compact (6 decimals matches
        # the precision the JSONL emitter writes — see naml.tokens).
        return {
            "cost_usd": round(self.cost_usd, 6),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "turns": self.turns,
        }


@dataclass
class SliceMetrics:
    """Running totals for one slice. ``ctx_pct`` is the *last seen* value
    (it's a point-in-time gauge, not something to sum)."""

    slice_id: str
    sprint_id: str = ""
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    turns: int = 0
    ctx_pct: int = 0
    last_event_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "sprint_id": self.sprint_id,
            "cost_usd": round(self.cost_usd, 6),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "turns": self.turns,
            "ctx_pct": self.ctx_pct,
            "last_event_at": self.last_event_at,
        }


@dataclass
class SprintMetrics:
    """Σ over every slice's events tagged with this sprint."""

    sprint_id: str
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    turns: int = 0
    last_event_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sprint_id": self.sprint_id,
            "cost_usd": round(self.cost_usd, 6),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "turns": self.turns,
            "last_event_at": self.last_event_at,
        }


@dataclass
class ProjectMetrics:
    """Project-level rollup. Daily buckets feed today/week/30d on snapshot;
    a separate ``lifetime`` running total keeps lifetime O(1) regardless
    of how old the project is."""

    lifetime: WindowTotals = field(default_factory=WindowTotals)
    # UTC YYYY-MM-DD -> totals
    daily: dict[str, WindowTotals] = field(default_factory=dict)
    started_at: str = ""
    last_event_at: str = ""


class Aggregator:
    """Mutable in-memory aggregate. The single source of truth at runtime.

    Lifecycle:

    1. Server boot → ``Aggregator()`` → cold-start replay calls
       :meth:`apply_event` for every line in every JSONL file → snapshot is
       warm.
    2. Watcher reads new lines from the tail of each JSONL file and calls
       :meth:`apply_event` again.
    3. ``GET /aggregates`` → :meth:`snapshot`.
    4. ``POST /aggregates/reset`` → :meth:`reset` + full re-replay by the
       caller (the aggregator itself does not touch disk).
    """

    def __init__(self) -> None:
        self.per_slice: dict[str, SliceMetrics] = {}
        self.per_sprint: dict[str, SprintMetrics] = {}
        self.per_project: ProjectMetrics = ProjectMetrics()
        self._lock = threading.Lock()

    # --- mutation ----------------------------------------------------

    def apply_event(self, event: dict, *, sprint_id: str = "") -> bool:
        """Fold one JSONL event into the aggregates. O(1).

        Returns ``True`` if the event was applied. A malformed or
        non-token event (missing ``slice`` / numeric fields) returns
        ``False`` without raising — caller can keep replaying.
        """
        if not isinstance(event, dict):
            return False
        slice_id = event.get("slice")
        if not isinstance(slice_id, str) or not slice_id:
            return False

        try:
            tokens_in = int(event.get("tokens_in", 0) or 0)
            tokens_out = int(event.get("tokens_out", 0) or 0)
            cache_read = int(event.get("cache_read", 0) or 0)
            cache_write = int(event.get("cache_write", 0) or 0)
            cost_usd = float(event.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        ctx_pct_raw = event.get("ctx_pct", 0)
        try:
            ctx_pct = int(ctx_pct_raw)
        except (TypeError, ValueError):
            ctx_pct = 0

        t_raw = event.get("t", "")
        t_dt = _parse_event_time(t_raw)
        t_str = t_raw if isinstance(t_raw, str) else ""

        with self._lock:
            # --- per_slice
            slice_m = self.per_slice.get(slice_id)
            if slice_m is None:
                slice_m = SliceMetrics(slice_id=slice_id, sprint_id=sprint_id)
                self.per_slice[slice_id] = slice_m
            # Late-bind sprint_id if we didn't know it the first time
            # (the watcher always knows it; cold-start does too).
            if sprint_id and not slice_m.sprint_id:
                slice_m.sprint_id = sprint_id
            slice_m.cost_usd += cost_usd
            slice_m.tokens_in += tokens_in
            slice_m.tokens_out += tokens_out
            slice_m.cache_read += cache_read
            slice_m.cache_write += cache_write
            slice_m.turns += 1
            slice_m.ctx_pct = ctx_pct
            if t_str:
                slice_m.last_event_at = t_str

            # --- per_sprint
            if sprint_id:
                sprint_m = self.per_sprint.get(sprint_id)
                if sprint_m is None:
                    sprint_m = SprintMetrics(sprint_id=sprint_id)
                    self.per_sprint[sprint_id] = sprint_m
                sprint_m.cost_usd += cost_usd
                sprint_m.tokens_in += tokens_in
                sprint_m.tokens_out += tokens_out
                sprint_m.cache_read += cache_read
                sprint_m.cache_write += cache_write
                sprint_m.turns += 1
                if t_str:
                    sprint_m.last_event_at = t_str

            # --- per_project lifetime
            life = self.per_project.lifetime
            life.cost_usd += cost_usd
            life.tokens_in += tokens_in
            life.tokens_out += tokens_out
            life.cache_read += cache_read
            life.cache_write += cache_write
            life.turns += 1
            if t_str:
                self.per_project.last_event_at = t_str
                if not self.per_project.started_at:
                    self.per_project.started_at = t_str

            # --- per_project daily bucket
            if t_dt is not None:
                day = _date_key(t_dt)
                bucket = self.per_project.daily.get(day)
                if bucket is None:
                    bucket = WindowTotals()
                    self.per_project.daily[day] = bucket
                bucket.cost_usd += cost_usd
                bucket.tokens_in += tokens_in
                bucket.tokens_out += tokens_out
                bucket.cache_read += cache_read
                bucket.cache_write += cache_write
                bucket.turns += 1

        return True

    def reset(self) -> None:
        """Drop all in-memory aggregates. Caller is responsible for replaying."""
        with self._lock:
            self.per_slice.clear()
            self.per_sprint.clear()
            self.per_project = ProjectMetrics()

    # --- read --------------------------------------------------------

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Serialise the current aggregates into the shape the SSE +
        ``GET /aggregates`` consumers expect.

        ``now`` is injectable for tests that pin time windows; default
        is ``datetime.now(UTC)``.
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

        with self._lock:
            today_key = now.date().isoformat()
            today = self.per_project.daily.get(today_key, WindowTotals()).to_dict()

            week = _sum_window(self.per_project.daily, now.date(), WEEK_DAYS)
            last_30d = _sum_window(self.per_project.daily, now.date(), MONTH_DAYS)
            lifetime = self.per_project.lifetime.to_dict()

            return {
                "per_slice": {
                    sid: m.to_dict() for sid, m in self.per_slice.items()
                },
                "per_sprint": {
                    sid: m.to_dict() for sid, m in self.per_sprint.items()
                },
                "per_project": {
                    "today": today,
                    "this_week": week,
                    "last_30d": last_30d,
                    "lifetime": {
                        **lifetime,
                        "started_at": self.per_project.started_at,
                    },
                    "last_event_at": self.per_project.last_event_at,
                },
            }


def _sum_window(daily: dict[str, WindowTotals], end: date, days: int) -> dict[str, Any]:
    """Sum daily buckets across the last ``days`` calendar days ending on
    ``end`` (inclusive). Returns the same shape as ``WindowTotals.to_dict``.

    O(days) — for the windows we expose (7, 30) this is effectively O(1).
    """
    total = WindowTotals()
    for i in range(days):
        day = (end - timedelta(days=i)).isoformat()
        bucket = daily.get(day)
        if bucket is not None:
            total.add(bucket)
    return total.to_dict()


# --- ergonomics: dataclass round-trip for tests ---------------------------
#
# The tests want to compare snapshots structurally. Provide an `asdict`-style
# helper so callers can convert SliceMetrics/SprintMetrics into plain dicts
# without depending on the dataclass module directly.

def metrics_as_dict(obj: Any) -> dict[str, Any]:
    """Return a plain-dict view of a SliceMetrics / SprintMetrics / WindowTotals.

    Falls back to ``dataclasses.asdict`` for anything else dataclass-shaped.
    """
    if isinstance(obj, (SliceMetrics, SprintMetrics)):
        return obj.to_dict()
    if isinstance(obj, WindowTotals):
        return obj.to_dict()
    return asdict(obj)
