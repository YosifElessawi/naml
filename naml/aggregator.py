"""In-memory rollups of the per-slice token JSONL stream.

The cockpit's headline numbers (today $ · this week · 30d · lifetime) are
derived from one JSONL line per Claude turn, written by
:mod:`naml.tokens`. This module owns the running aggregates: per slice,
per sprint, and per project.

Design choices (``artifacts/spec.md`` Q8b):

- **No DB.** All state is in RAM. Cold start rebuilds from JSONL.
- **O(1) per event.** ``apply_event`` is a fixed handful of dict
  lookups + adds. Time-window rollups are computed at read time from a
  bounded set of UTC daily buckets (≤ ~30 buckets ever matter).
- **UTC everywhere.** A "day" is a UTC calendar day. Local-time
  presentation lives in the browser. DST is irrelevant here.
- **Two view shapes.**

  - :meth:`Aggregator.snapshot` returns the slice-10 shape used by the
    ``GET /aggregates`` endpoint and the SSE ``snapshot`` payload.
  - :attr:`Aggregator.project` returns a flat-field
    :class:`ProjectView` (today_cost, week_cost, last_30d_cost,
    lifetime_cost, …) consumed by slice-12's metric-tick rollups and
    the cockpit's right-rail Cost Timeline block. Both views read from
    the same underlying buckets.

- **Lifetime origin.** Earliest timestamp ever observed (across cold-
  start replay or out-of-order writes), exposed via
  ``per_project.started_at`` and ``project.first_event_at``.
- **Metric-tick fan-out.** When an ``on_event`` callback is supplied,
  every successful :meth:`apply_event` builds a
  :class:`MetricTickPayload` and hands it to the subscriber for SSE
  publication (see :mod:`naml.metric_tick`).

Thread-safe: :meth:`apply_event`, :meth:`snapshot`, :meth:`reset`, and
the :attr:`project` view all hold an internal re-entrant lock so the
watcher thread can append events while the aiohttp request loop reads
state.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


log = logging.getLogger("naml.aggregator")

UTC = timezone.utc

# Rolling-window sizes used by :meth:`Aggregator.snapshot`.
WEEK_DAYS = 7
MONTH_DAYS = 30

# Monday week start for the slice-12 ``project.week_*`` calendar-week view
# (per ``artifacts/spec.md`` Q8b). The snapshot's ``this_week`` field keeps
# the rolling-7-days definition for backwards compatibility with slice-10.
_WEEK_START_WEEKDAY = 0  # Monday


def _parse_event_time(value: Any) -> datetime | None:
    """Parse the ``t`` field on a token event. ``None`` if unparseable.

    Tolerates the legacy ``Z`` suffix and naive ISO strings (promoted to
    UTC) so a one-off fixture or older JSONL line can't poison cold-start.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        v = value
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _date_key(dt: datetime) -> str:
    """UTC ``YYYY-MM-DD`` bucket key for an aware datetime."""
    return dt.astimezone(UTC).date().isoformat()


def _week_start(d: date, weekday: int = _WEEK_START_WEEKDAY) -> date:
    """Return the start-of-week UTC date containing ``d`` (default Monday)."""
    days_back = (d.weekday() - weekday) % 7
    return d - timedelta(days=days_back)


def _round6(value: float) -> float:
    """Round a USD figure to micro-dollars to bound float drift."""
    return round(value, 6)


def _safe_parse_iso_date(key: str) -> date | None:
    try:
        return date.fromisoformat(key)
    except ValueError:
        return None


@dataclass
class WindowTotals:
    """Rolled-up counters for one daily bucket or one of the named windows
    on :meth:`Aggregator.snapshot`."""

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

    @property
    def tokens_total(self) -> int:
        return (
            self.tokens_in + self.tokens_out + self.cache_read + self.cache_write
        )

    def to_dict(self) -> dict[str, Any]:
        # 6 decimal places matches the precision naml.tokens writes.
        return {
            "cost_usd": _round6(self.cost_usd),
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

    @property
    def tokens_total(self) -> int:
        return (
            self.tokens_in + self.tokens_out + self.cache_read + self.cache_write
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "sprint_id": self.sprint_id,
            "cost_usd": _round6(self.cost_usd),
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

    @property
    def tokens_total(self) -> int:
        return (
            self.tokens_in + self.tokens_out + self.cache_read + self.cache_write
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sprint_id": self.sprint_id,
            "cost_usd": _round6(self.cost_usd),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "turns": self.turns,
            "last_event_at": self.last_event_at,
        }


@dataclass
class ProjectMetrics:
    """Project-level rollup. Daily buckets feed the windowed views; a
    separate ``lifetime`` running total keeps lifetime O(1) regardless of
    how old the project is."""

    lifetime: WindowTotals = field(default_factory=WindowTotals)
    # UTC YYYY-MM-DD -> totals. String-keyed for easy snapshot equality.
    daily: dict[str, WindowTotals] = field(default_factory=dict)
    started_at: str = ""
    last_event_at: str = ""


@dataclass
class ProjectView:
    """Slice-12 flat-field projection of :class:`ProjectMetrics`.

    Computed lazily on every :attr:`Aggregator.project` access so day
    rollover is automatic — the view always reflects the current UTC
    clock without needing an out-of-band recompute step.

    All cost fields are rounded to 6 decimals. ``today_tokens`` etc. are
    the sum of all four token categories (in / out / cache_read /
    cache_write) so the cockpit's "tokens" headline is a single number.
    """

    today_cost: float = 0.0
    today_tokens: int = 0
    week_cost: float = 0.0
    week_tokens: int = 0
    last_30d_cost: float = 0.0
    last_30d_tokens: int = 0
    lifetime_cost: float = 0.0
    lifetime_tokens: int = 0
    first_event_at: str = ""
    last_event_at: str = ""


@dataclass
class MetricTickPayload:
    """Payload assembled per :meth:`Aggregator.apply_event` and shipped
    down the ``metric-tick`` SSE channel (see :mod:`naml.metric_tick`).
    Shape matches the slice-12 spec contract."""

    slice_id: str
    sprint_id: str
    delta: dict[str, Any]
    rollups: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice": self.slice_id,
            "sprint": self.sprint_id,
            "delta": self.delta,
            "rollups": self.rollups,
        }


class Aggregator:
    """Mutable in-memory aggregate. Single source of truth at runtime.

    Lifecycle:

    1. Server boot → ``Aggregator()`` → cold-start replay via the watcher
       (or :meth:`replay_jsonl`) calls :meth:`apply_event` for every line
       in every JSONL file → snapshot is warm.
    2. Watcher reads new lines from the tail of each JSONL file and calls
       :meth:`apply_event` again.
    3. ``GET /aggregates`` → :meth:`snapshot`.
    4. ``POST /aggregates/reset`` → :meth:`reset` + full re-replay by the
       caller (the aggregator itself doesn't touch disk on reset).
    5. Once a minute the server fires :meth:`roll_day` so the ``today``
       window resets at UTC midnight even when no events fire across the
       boundary.

    Parameters
    ----------
    now_fn:
        Optional clock injection. Tests use it to advance across midnight
        and week boundaries deterministically.
    on_event:
        Optional callback invoked after every successful event with a
        :class:`MetricTickPayload`. Exceptions in the callback are
        logged but never propagate.
    """

    def __init__(
        self,
        *,
        now_fn: Callable[[], datetime] | None = None,
        on_event: Callable[[MetricTickPayload], None] | None = None,
    ) -> None:
        self.per_slice: dict[str, SliceMetrics] = {}
        self.per_sprint: dict[str, SprintMetrics] = {}
        self.per_project: ProjectMetrics = ProjectMetrics()
        self._lock = threading.RLock()
        self._now: Callable[[], datetime] = now_fn or (
            lambda: datetime.now(tz=UTC)
        )
        self._on_event = on_event
        self._today_utc: date = self._now_utc_date()

    # --- subscriber wiring ------------------------------------------------

    def set_on_event(
        self, callback: Callable[[MetricTickPayload], None] | None
    ) -> None:
        """Late-bind the per-event callback (used when the broadcaster
        comes up after the aggregator)."""
        self._on_event = callback

    # --- read API ---------------------------------------------------------

    @property
    def project(self) -> ProjectView:
        """Slice-12 flat-field view of project rollups. Always fresh."""
        with self._lock:
            return self._build_project_view()

    def _build_project_view(self) -> ProjectView:
        """Compute :class:`ProjectView` from current daily buckets and the
        lifetime running total. Caller MUST hold ``self._lock``."""
        today = self._now_utc_date()
        week_start = _week_start(today)
        thirty_floor = today - timedelta(days=30)

        v = ProjectView()
        for day_key, bucket in self.per_project.daily.items():
            d = _safe_parse_iso_date(day_key)
            if d is None:
                continue
            tokens_total = bucket.tokens_total
            if d == today:
                v.today_cost += bucket.cost_usd
                v.today_tokens += tokens_total
            if d >= week_start:
                v.week_cost += bucket.cost_usd
                v.week_tokens += tokens_total
            if d >= thirty_floor:
                v.last_30d_cost += bucket.cost_usd
                v.last_30d_tokens += tokens_total

        v.today_cost = _round6(v.today_cost)
        v.week_cost = _round6(v.week_cost)
        v.last_30d_cost = _round6(v.last_30d_cost)
        v.lifetime_cost = _round6(self.per_project.lifetime.cost_usd)
        v.lifetime_tokens = self.per_project.lifetime.tokens_total
        v.first_event_at = self.per_project.started_at
        v.last_event_at = self.per_project.last_event_at
        return v

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Serialise current aggregates into the slice-10 wire shape used
        by ``GET /aggregates`` and the SSE ``snapshot`` payload.

        ``now`` is injectable for tests that pin time windows; defaults to
        :attr:`Aggregator._now` so a slice-12 ``now_fn`` clock applies.
        """
        if now is None:
            now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        else:
            now = now.astimezone(UTC)

        with self._lock:
            today_key = now.date().isoformat()
            today = self.per_project.daily.get(
                today_key, WindowTotals()
            ).to_dict()
            week = _sum_window(self.per_project.daily, now.date(), WEEK_DAYS)
            last_30d = _sum_window(
                self.per_project.daily, now.date(), MONTH_DAYS
            )
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

    # --- mutation --------------------------------------------------------

    def reset(self) -> None:
        """Drop all in-memory aggregates. Caller is responsible for
        replaying disk state if a fresh aggregator is wanted."""
        with self._lock:
            self.per_slice.clear()
            self.per_sprint.clear()
            self.per_project = ProjectMetrics()
        self._today_utc = self._now_utc_date()

    def apply_event(
        self, event: Any, *, sprint_id: str = ""
    ) -> MetricTickPayload | None:
        """Fold one JSONL event into the aggregates. O(1).

        Returns a :class:`MetricTickPayload` on success (truthy) or
        ``None`` if the event was malformed / missing a slice id (falsy).
        Callers that only need a boolean (the watcher does ``if
        apply_event(...):``) keep working unchanged.
        """
        if not isinstance(event, dict):
            return None
        slice_id = event.get("slice")
        if not isinstance(slice_id, str) or not slice_id:
            return None

        try:
            tokens_in = int(event.get("tokens_in", 0) or 0)
            tokens_out = int(event.get("tokens_out", 0) or 0)
            cache_read = int(event.get("cache_read", 0) or 0)
            cache_write = int(event.get("cache_write", 0) or 0)
            cost_usd = float(event.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        try:
            ctx_pct = int(event.get("ctx_pct", 0) or 0)
        except (TypeError, ValueError):
            ctx_pct = 0

        t_raw = event.get("t", "")
        t_dt = _parse_event_time(t_raw)
        t_str = t_raw if isinstance(t_raw, str) else ""

        # Self-heal across midnight: detect a UTC day flip in case the
        # cron tick hasn't fired between events. ``roll_day`` is a no-op
        # when same-day.
        self.roll_day()

        with self._lock:
            # --- per_slice -------------------------------------------
            slice_m = self.per_slice.get(slice_id)
            if slice_m is None:
                slice_m = SliceMetrics(slice_id=slice_id, sprint_id=sprint_id)
                self.per_slice[slice_id] = slice_m
            # Late-bind sprint_id if it wasn't known the first time.
            if sprint_id and not slice_m.sprint_id:
                slice_m.sprint_id = sprint_id
            slice_m.cost_usd = _round6(slice_m.cost_usd + cost_usd)
            slice_m.tokens_in += tokens_in
            slice_m.tokens_out += tokens_out
            slice_m.cache_read += cache_read
            slice_m.cache_write += cache_write
            slice_m.turns += 1
            slice_m.ctx_pct = ctx_pct
            if t_str and (not slice_m.last_event_at or t_str > slice_m.last_event_at):
                slice_m.last_event_at = t_str

            # --- per_sprint ------------------------------------------
            effective_sprint = slice_m.sprint_id or sprint_id
            sprint_m: SprintMetrics | None = None
            if effective_sprint:
                sprint_m = self.per_sprint.get(effective_sprint)
                if sprint_m is None:
                    sprint_m = SprintMetrics(sprint_id=effective_sprint)
                    self.per_sprint[effective_sprint] = sprint_m
                sprint_m.cost_usd = _round6(sprint_m.cost_usd + cost_usd)
                sprint_m.tokens_in += tokens_in
                sprint_m.tokens_out += tokens_out
                sprint_m.cache_read += cache_read
                sprint_m.cache_write += cache_write
                sprint_m.turns += 1
                if t_str and (
                    not sprint_m.last_event_at or t_str > sprint_m.last_event_at
                ):
                    sprint_m.last_event_at = t_str

            # --- per_project lifetime --------------------------------
            life = self.per_project.lifetime
            life.cost_usd = _round6(life.cost_usd + cost_usd)
            life.tokens_in += tokens_in
            life.tokens_out += tokens_out
            life.cache_read += cache_read
            life.cache_write += cache_write
            life.turns += 1
            if t_str:
                # ``started_at`` tracks the earliest observed timestamp so
                # out-of-order replay (later then earlier) still pins the
                # project origin to the true first event.
                if (
                    not self.per_project.started_at
                    or t_str < self.per_project.started_at
                ):
                    self.per_project.started_at = t_str
                if (
                    not self.per_project.last_event_at
                    or t_str > self.per_project.last_event_at
                ):
                    self.per_project.last_event_at = t_str

            # --- per_project daily bucket ----------------------------
            if t_dt is not None:
                day_key = _date_key(t_dt)
                bucket = self.per_project.daily.get(day_key)
                if bucket is None:
                    bucket = WindowTotals()
                    self.per_project.daily[day_key] = bucket
                bucket.cost_usd = _round6(bucket.cost_usd + cost_usd)
                bucket.tokens_in += tokens_in
                bucket.tokens_out += tokens_out
                bucket.cache_read += cache_read
                bucket.cache_write += cache_write
                bucket.turns += 1

            # Compute the metric-tick rollups while still under the lock
            # so they reflect a consistent post-apply snapshot.
            view = self._build_project_view()
            rollups = {
                "slice_cost": slice_m.cost_usd,
                "slice_tokens_in": slice_m.tokens_in,
                "slice_tokens_out": slice_m.tokens_out,
                "slice_cache_read": slice_m.cache_read,
                "slice_cache_write": slice_m.cache_write,
                "slice_ctx_pct": slice_m.ctx_pct,
                "sprint_cost": sprint_m.cost_usd if sprint_m else 0.0,
                "sprint_tokens": sprint_m.tokens_total if sprint_m else 0,
                "project_today": view.today_cost,
                "project_today_tokens": view.today_tokens,
                "project_week": view.week_cost,
                "project_week_tokens": view.week_tokens,
                "project_30d": view.last_30d_cost,
                "project_30d_tokens": view.last_30d_tokens,
                "project_lifetime": view.lifetime_cost,
                "project_lifetime_tokens": view.lifetime_tokens,
            }

        payload = MetricTickPayload(
            slice_id=slice_id,
            sprint_id=effective_sprint,
            delta=event,
            rollups=rollups,
        )
        if self._on_event is not None:
            try:
                self._on_event(payload)
            except Exception:  # noqa: BLE001 — never let a subscriber kill apply_event
                log.exception("aggregator on_event callback failed")
        return payload

    def roll_day(self) -> bool:
        """Detect a UTC-day change and evict daily buckets older than the
        30-day window so memory stays bounded.

        Returns ``True`` only when the day actually flipped. Idempotent;
        called by :func:`naml.metric_tick.daily_rollover_loop` once per
        minute, and pre-emptively by :meth:`apply_event` so a long gap
        between events never leaves a stale ``today`` bar.
        """
        today = self._now_utc_date()
        if today == self._today_utc:
            return False
        thirty_floor = today - timedelta(days=30)
        with self._lock:
            expired = [
                k
                for k in self.per_project.daily
                if (parsed := _safe_parse_iso_date(k)) is not None
                and parsed < thirty_floor
            ]
            for k in expired:
                del self.per_project.daily[k]
        self._today_utc = today
        return True

    # --- replay ----------------------------------------------------------

    def replay_jsonl(
        self, path: Path, *, sprint_id: str = "", strict: bool = False
    ) -> int:
        """Replay events from a JSONL file into the aggregator.

        ``strict=False`` (default): malformed lines are logged and
        skipped so one bad line in a 6-month-old project doesn't kill
        cold-start. ``strict=True`` raises ``json.JSONDecodeError`` —
        used by tests.

        Complementary to :func:`naml.watcher.replay_all`, which walks an
        entire sprints tree; this method handles a single file and is
        the entry point slice-12's cold-start regression tests use.
        """
        if not path.is_file():
            return 0
        applied = 0
        with path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    if strict:
                        raise
                    log.warning(
                        "aggregator: skipping malformed JSONL line in %s", path
                    )
                    continue
                if not isinstance(ev, dict):
                    continue
                if self.apply_event(ev, sprint_id=sprint_id) is not None:
                    applied += 1
        return applied

    def replay_paths(self, items: Iterable[tuple[Path, str]]) -> int:
        """Replay multiple ``(path, sprint_id)`` pairs. Returns total applied."""
        total = 0
        for path, sprint_id in items:
            total += self.replay_jsonl(path, sprint_id=sprint_id)
        return total

    # --- internal --------------------------------------------------------

    def _now_utc_date(self) -> date:
        n = self._now()
        if n.tzinfo is None:
            n = n.replace(tzinfo=UTC)
        return n.astimezone(UTC).date()


def _sum_window(
    daily: dict[str, WindowTotals], end: date, days: int
) -> dict[str, Any]:
    """Sum daily buckets across the last ``days`` calendar days ending on
    ``end`` (inclusive). Same shape as :meth:`WindowTotals.to_dict`.

    O(days) — for the windows we expose (7, 30) this is effectively O(1).
    """
    total = WindowTotals()
    for i in range(days):
        day = (end - timedelta(days=i)).isoformat()
        bucket = daily.get(day)
        if bucket is not None:
            total.add(bucket)
    return total.to_dict()


def metrics_as_dict(obj: Any) -> dict[str, Any]:
    """Return a plain-dict view of a SliceMetrics / SprintMetrics /
    WindowTotals / MetricTickPayload. Falls back to ``dataclasses.asdict``
    for anything else dataclass-shaped."""
    if isinstance(obj, (SliceMetrics, SprintMetrics)):
        return obj.to_dict()
    if isinstance(obj, WindowTotals):
        return obj.to_dict()
    if isinstance(obj, MetricTickPayload):
        return obj.to_dict()
    return asdict(obj)
