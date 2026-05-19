"""Tests for naml.aggregator — in-memory metrics rollup + time-window views.

Covers the union of slice-10 (data shape + ``snapshot``) and slice-12
(``project`` flat-field view + ``roll_day`` + ``MetricTickPayload`` +
``replay_jsonl``) acceptance criteria:

- ``apply_event`` is O(1) per event (asserted by wall-clock on 10k events).
- per_slice / per_sprint / per_project totals match expected sums.
- today / this_week / last_30d / lifetime windows are computed from UTC
  daily buckets (snapshot shape) and from the live ``project`` view.
- UTC day rollover resets ``today`` only; week/30d/lifetime keep
  accumulating across midnight.
- ``MetricTickPayload`` returned by ``apply_event`` carries the rollups
  contract slice-12 ships to the cockpit.
- ``reset`` clears all aggregates; the caller can re-replay.
- ``snapshot`` exposes the shape the SSE consumer (slice-12) needs.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from naml.aggregator import (
    MONTH_DAYS,
    WEEK_DAYS,
    Aggregator,
    MetricTickPayload,
    ProjectMetrics,
    SliceMetrics,
    SprintMetrics,
    WindowTotals,
)


UTC = timezone.utc


def _event(
    *,
    t: datetime,
    slice_id: str,
    session: str = "sess",
    turn: int = 1,
    tokens_in: int = 100,
    tokens_out: int = 50,
    cache_read: int = 0,
    cache_write: int = 0,
    cost_usd: float = 0.01,
    ctx_pct: int = 10,
) -> dict:
    """Build a token-event dict matching the JSONL schema in spec Q8b.

    Used by the slice-10 test surface (named ``cost_usd``).
    """
    return {
        "t": t.astimezone(timezone.utc).isoformat(),
        "slice": slice_id,
        "session": session,
        "turn": turn,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "cost_usd": cost_usd,
        "ctx_pct": ctx_pct,
    }


def _ev(
    *,
    t: datetime,
    slice_id: str = "slice-1",
    cost: float = 0.10,
    tokens_in: int = 1_000,
    tokens_out: int = 200,
    cache_read: int = 50_000,
    cache_write: int = 0,
    ctx_pct: int = 25,
    session: str = "abc",
    turn: int = 1,
) -> dict:
    """Slice-12 test helper. Same shape as :func:`_event` but with
    different defaults (cache-heavy turn) and a shorter parameter name."""
    return {
        "t": t.isoformat(),
        "slice": slice_id,
        "session": session,
        "turn": turn,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "cost_usd": cost,
        "ctx_pct": ctx_pct,
    }


# --- slice-10 surface -------------------------------------------------------


class ApplyEventTests(unittest.TestCase):
    def test_first_event_creates_slice_sprint_project(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        ev = _event(
            t=t, slice_id="slice-1",
            tokens_in=200, tokens_out=80, cache_read=1_000, cost_usd=0.02,
            ctx_pct=33,
        )
        self.assertTrue(agg.apply_event(ev, sprint_id="sprint-A"))

        self.assertEqual(agg.per_slice["slice-1"].slice_id, "slice-1")
        self.assertEqual(agg.per_slice["slice-1"].sprint_id, "sprint-A")
        self.assertEqual(agg.per_slice["slice-1"].tokens_in, 200)
        self.assertEqual(agg.per_slice["slice-1"].cache_read, 1_000)
        self.assertEqual(agg.per_slice["slice-1"].ctx_pct, 33)
        self.assertEqual(agg.per_slice["slice-1"].turns, 1)

        self.assertEqual(agg.per_sprint["sprint-A"].tokens_in, 200)
        self.assertEqual(agg.per_sprint["sprint-A"].turns, 1)

        self.assertAlmostEqual(agg.per_project.lifetime.cost_usd, 0.02, places=6)
        self.assertEqual(agg.per_project.lifetime.tokens_in, 200)
        self.assertEqual(agg.per_project.started_at, ev["t"])

    def test_multiple_events_accumulate(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        for i in range(100):
            agg.apply_event(
                _event(
                    t=t + timedelta(seconds=i),
                    slice_id="slice-1",
                    tokens_in=10, tokens_out=5, cache_read=100,
                    cost_usd=0.001,
                ),
                sprint_id="sprint-A",
            )

        sm = agg.per_slice["slice-1"]
        self.assertEqual(sm.tokens_in, 1000)
        self.assertEqual(sm.tokens_out, 500)
        self.assertEqual(sm.cache_read, 10_000)
        self.assertEqual(sm.turns, 100)
        self.assertAlmostEqual(sm.cost_usd, 0.1, places=6)

    def test_different_slices_aggregate_into_same_sprint(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        agg.apply_event(_event(t=t, slice_id="slice-1", tokens_in=100), sprint_id="S")
        agg.apply_event(_event(t=t, slice_id="slice-2", tokens_in=300), sprint_id="S")

        self.assertEqual(agg.per_sprint["S"].tokens_in, 400)
        self.assertEqual(agg.per_sprint["S"].turns, 2)
        self.assertEqual(set(agg.per_slice.keys()), {"slice-1", "slice-2"})

    def test_event_without_slice_is_rejected(self) -> None:
        agg = Aggregator()
        self.assertFalse(agg.apply_event({}))
        self.assertFalse(agg.apply_event({"slice": ""}))
        self.assertFalse(agg.apply_event({"slice": 123, "tokens_in": 1}))
        self.assertEqual(agg.per_slice, {})

    def test_garbage_field_types_dont_crash(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        # Malformed tokens_in (string) — current code rejects the whole event.
        self.assertFalse(
            agg.apply_event(
                {**_event(t=t, slice_id="slice-1"), "tokens_in": "huge"}
            )
        )

    def test_event_without_sprint_still_records_slice_and_project(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        # No sprint_id arg — slice still recorded; per_sprint stays empty.
        self.assertTrue(agg.apply_event(_event(t=t, slice_id="slice-x")))
        self.assertIn("slice-x", agg.per_slice)
        self.assertEqual(agg.per_sprint, {})
        self.assertGreater(agg.per_project.lifetime.turns, 0)


class WindowTotalsTests(unittest.TestCase):
    """The today / week / 30d / lifetime split is the cockpit headline.

    These tests use a frozen ``now`` so we can plant events at known
    distances and assert which windows they fall into. They exercise the
    slice-10 ``snapshot`` shape (rolling 7-day ``this_week``).
    """

    def setUp(self) -> None:
        self.agg = Aggregator()
        self.now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)

    def _add(self, *, days_ago: int, cost: float, tokens_in: int = 100) -> None:
        t = self.now - timedelta(days=days_ago)
        self.agg.apply_event(
            _event(
                t=t,
                slice_id=f"slice-{days_ago}",
                tokens_in=tokens_in,
                cost_usd=cost,
            ),
            sprint_id=f"sprint-{days_ago}",
        )

    def test_today_is_zero_when_no_events(self) -> None:
        snap = self.agg.snapshot(now=self.now)
        self.assertEqual(snap["per_project"]["today"]["cost_usd"], 0.0)
        self.assertEqual(snap["per_project"]["this_week"]["cost_usd"], 0.0)

    def test_today_includes_today_excludes_yesterday(self) -> None:
        self._add(days_ago=0, cost=0.50)
        self._add(days_ago=1, cost=0.25)
        snap = self.agg.snapshot(now=self.now)
        self.assertAlmostEqual(snap["per_project"]["today"]["cost_usd"], 0.50)

    def test_week_includes_last_seven_days(self) -> None:
        for d in range(WEEK_DAYS):
            self._add(days_ago=d, cost=0.10)
        # An event 7 days ago is outside the 7-day window (day 0..6 inclusive).
        self._add(days_ago=WEEK_DAYS, cost=0.99)

        snap = self.agg.snapshot(now=self.now)
        self.assertAlmostEqual(
            snap["per_project"]["this_week"]["cost_usd"],
            0.10 * WEEK_DAYS,
            places=6,
        )

    def test_30d_includes_last_thirty_days(self) -> None:
        self._add(days_ago=0, cost=1.0)
        self._add(days_ago=29, cost=2.0)
        self._add(days_ago=MONTH_DAYS, cost=99.0)  # outside

        snap = self.agg.snapshot(now=self.now)
        self.assertAlmostEqual(snap["per_project"]["last_30d"]["cost_usd"], 3.0)

    def test_lifetime_includes_all_events(self) -> None:
        self._add(days_ago=0, cost=1.0)
        self._add(days_ago=400, cost=2.0)
        snap = self.agg.snapshot(now=self.now)
        self.assertAlmostEqual(snap["per_project"]["lifetime"]["cost_usd"], 3.0)

    def test_dst_neutral_uses_utc_throughout(self) -> None:
        """A spring-forward DST day must not lose / gain an event.

        We intentionally cross 2026-03-08 (US DST start) with two events:
        one just before and one just after the transition. Both must
        land in the appropriate UTC day bucket regardless of any
        timezone the runner happens to be in.
        """
        agg = Aggregator()
        # 02:30 UTC the morning DST starts in the US:
        before = datetime(2026, 3, 8, 2, 30, 0, tzinfo=timezone.utc)
        # 12 hours later, same UTC day:
        same_day = datetime(2026, 3, 8, 14, 30, 0, tzinfo=timezone.utc)
        # Next UTC day:
        next_day = datetime(2026, 3, 9, 14, 30, 0, tzinfo=timezone.utc)

        for t in (before, same_day, next_day):
            agg.apply_event(
                _event(t=t, slice_id="slice-x", cost_usd=1.0),
                sprint_id="S",
            )

        # "today" pinned to 2026-03-08 must be exactly 2.0 (two events).
        now = datetime(2026, 3, 8, 23, 0, 0, tzinfo=timezone.utc)
        snap = agg.snapshot(now=now)
        self.assertAlmostEqual(snap["per_project"]["today"]["cost_usd"], 2.0)

        # Pinned to 2026-03-09 must be exactly 1.0 (next_day only).
        snap2 = agg.snapshot(
            now=datetime(2026, 3, 9, 23, 0, 0, tzinfo=timezone.utc)
        )
        self.assertAlmostEqual(snap2["per_project"]["today"]["cost_usd"], 1.0)

    def test_naive_now_is_treated_as_utc(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        agg.apply_event(_event(t=t, slice_id="s", cost_usd=1.0), sprint_id="S")

        # Naive datetime should be promoted to UTC.
        snap = agg.snapshot(now=datetime(2026, 5, 19, 23, 0, 0))
        self.assertAlmostEqual(snap["per_project"]["today"]["cost_usd"], 1.0)


class SnapshotShapeTests(unittest.TestCase):
    def test_snapshot_shape_matches_spec(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        agg.apply_event(_event(t=t, slice_id="slice-4"), sprint_id="sprint-A")

        snap = agg.snapshot(now=t)
        self.assertEqual(set(snap.keys()), {"per_slice", "per_sprint", "per_project"})

        self.assertIn("slice-4", snap["per_slice"])
        slice_snap = snap["per_slice"]["slice-4"]
        for key in (
            "slice_id", "sprint_id", "cost_usd", "tokens_in", "tokens_out",
            "cache_read", "cache_write", "turns", "ctx_pct", "last_event_at",
        ):
            self.assertIn(key, slice_snap)

        self.assertIn("sprint-A", snap["per_sprint"])
        sprint_snap = snap["per_sprint"]["sprint-A"]
        for key in (
            "sprint_id", "cost_usd", "tokens_in", "tokens_out",
            "cache_read", "cache_write", "turns", "last_event_at",
        ):
            self.assertIn(key, sprint_snap)

        proj = snap["per_project"]
        for key in ("today", "this_week", "last_30d", "lifetime", "last_event_at"):
            self.assertIn(key, proj)
        self.assertIn("started_at", proj["lifetime"])


class ResetTests(unittest.TestCase):
    def test_reset_clears_state(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        for i in range(10):
            agg.apply_event(
                _event(t=t, slice_id=f"slice-{i}", cost_usd=0.10),
                sprint_id="S",
            )
        self.assertGreater(len(agg.per_slice), 0)

        agg.reset()
        self.assertEqual(agg.per_slice, {})
        self.assertEqual(agg.per_sprint, {})
        self.assertEqual(agg.per_project.lifetime.cost_usd, 0.0)
        self.assertEqual(agg.per_project.daily, {})

    def test_reset_then_re_replay_matches(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        events = [
            _event(t=t + timedelta(seconds=i), slice_id="slice-1", cost_usd=0.01)
            for i in range(50)
        ]
        for ev in events:
            agg.apply_event(ev, sprint_id="S")
        snap1 = agg.snapshot(now=t)

        agg.reset()
        for ev in events:
            agg.apply_event(ev, sprint_id="S")
        snap2 = agg.snapshot(now=t)

        self.assertEqual(snap1, snap2)


class PerformanceTests(unittest.TestCase):
    """Confirm apply_event is O(1) — a 10k-event run should be fast enough
    to dispel any accidentally-O(n) loop hiding in the implementation."""

    def test_apply_event_is_fast(self) -> None:
        agg = Aggregator()
        t = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        events = [
            _event(
                t=t + timedelta(seconds=i % 86_400),
                slice_id=f"slice-{i % 10}",
                tokens_in=100 + i,
                cost_usd=0.001,
            )
            for i in range(10_000)
        ]

        start = time.perf_counter()
        for ev in events:
            agg.apply_event(ev, sprint_id=f"sprint-{ev['slice']}")
        elapsed = time.perf_counter() - start

        # 10k events in well under a second even on slow CI. Generous bound
        # so the test doesn't flake; the real concern is detecting an O(n)
        # regression, which would balloon this past 5s.
        self.assertLess(elapsed, 2.0, f"apply_event too slow: {elapsed:.3f}s")
        self.assertEqual(agg.per_project.lifetime.turns, 10_000)


class DataclassReprTests(unittest.TestCase):
    """Spot-check the dataclass dict serialisation used by snapshot()."""

    def test_window_totals_to_dict(self) -> None:
        w = WindowTotals(cost_usd=0.1234567, tokens_in=10, turns=2)
        d = w.to_dict()
        # Cost rounded to 6 places.
        self.assertEqual(d["cost_usd"], 0.123457)
        self.assertEqual(d["tokens_in"], 10)
        self.assertEqual(d["turns"], 2)

    def test_slice_and_sprint_metrics_to_dict(self) -> None:
        s = SliceMetrics(slice_id="x", sprint_id="y", tokens_in=10)
        self.assertEqual(s.to_dict()["slice_id"], "x")
        self.assertEqual(s.to_dict()["sprint_id"], "y")

        p = SprintMetrics(sprint_id="y", tokens_in=10)
        self.assertEqual(p.to_dict()["sprint_id"], "y")


# --- slice-12 surface -------------------------------------------------------


class _FrozenClock:
    """Manually-advanceable clock for deterministic boundary tests."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class TimeWindowBasicsTests(unittest.TestCase):
    def setUp(self) -> None:
        # Wednesday 2026-05-20 14:00 UTC. Week start (Mon) = 2026-05-18.
        self.clock = _FrozenClock(datetime(2026, 5, 20, 14, 0, tzinfo=UTC))
        self.agg = Aggregator(now_fn=self.clock)

    def test_today_event_lands_in_all_windows(self) -> None:
        payload = self.agg.apply_event(_ev(t=self.clock(), cost=0.50))
        assert payload is not None
        p = self.agg.project
        self.assertAlmostEqual(p.today_cost, 0.50)
        self.assertAlmostEqual(p.week_cost, 0.50)
        self.assertAlmostEqual(p.last_30d_cost, 0.50)
        self.assertAlmostEqual(p.lifetime_cost, 0.50)

    def test_yesterday_event_skips_today_only(self) -> None:
        yesterday = self.clock() - timedelta(days=1)  # still in same week
        self.agg.apply_event(_ev(t=yesterday, cost=0.30))
        p = self.agg.project
        self.assertAlmostEqual(p.today_cost, 0.0)
        self.assertAlmostEqual(p.week_cost, 0.30)
        self.assertAlmostEqual(p.last_30d_cost, 0.30)
        self.assertAlmostEqual(p.lifetime_cost, 0.30)

    def test_old_event_only_in_lifetime(self) -> None:
        ancient = self.clock() - timedelta(days=90)
        self.agg.apply_event(_ev(t=ancient, cost=0.25))
        p = self.agg.project
        self.assertAlmostEqual(p.today_cost, 0.0)
        self.assertAlmostEqual(p.week_cost, 0.0)
        self.assertAlmostEqual(p.last_30d_cost, 0.0)
        self.assertAlmostEqual(p.lifetime_cost, 0.25)

    def test_lifetime_origin_pinned_to_first_event(self) -> None:
        early = self.clock() - timedelta(days=10)
        later = self.clock() - timedelta(days=1)
        self.agg.apply_event(_ev(t=later, cost=0.10))
        self.agg.apply_event(_ev(t=early, cost=0.20))  # apply out-of-order
        # first_event_at should be the earliest observed timestamp.
        self.assertTrue(self.agg.project.first_event_at.startswith("2026-05-10"))

    def test_week_boundary_monday_start(self) -> None:
        # An event on Sunday 2026-05-17 is the PREVIOUS week (Mon-Sun ISO).
        # Current week (per spec.md Monday start) = 2026-05-18 → 2026-05-24.
        sunday = datetime(2026, 5, 17, 23, 59, tzinfo=UTC)
        self.agg.apply_event(_ev(t=sunday, cost=0.30))
        self.assertAlmostEqual(self.agg.project.week_cost, 0.0)
        self.assertAlmostEqual(self.agg.project.last_30d_cost, 0.30)

        monday = datetime(2026, 5, 18, 0, 1, tzinfo=UTC)
        self.agg.apply_event(_ev(t=monday, cost=0.40))
        self.assertAlmostEqual(self.agg.project.week_cost, 0.40)


class DayRolloverTests(unittest.TestCase):
    """Simulates the user running a sprint across midnight UTC — today resets,
    week/30d/lifetime keep accumulating."""

    def setUp(self) -> None:
        # 2026-05-20 23:55 UTC — five minutes before midnight.
        self.clock = _FrozenClock(datetime(2026, 5, 20, 23, 55, tzinfo=UTC))
        self.agg = Aggregator(now_fn=self.clock)

    def test_midnight_rollover_resets_today_only(self) -> None:
        # Three turns before midnight.
        for i in range(3):
            self.agg.apply_event(
                _ev(t=self.clock(), cost=0.10, turn=i + 1)
            )
        p = self.agg.project
        self.assertAlmostEqual(p.today_cost, 0.30)
        self.assertAlmostEqual(p.week_cost, 0.30)
        self.assertAlmostEqual(p.lifetime_cost, 0.30)

        # Cross midnight. The cron tick fires from server.py.
        self.clock.advance(timedelta(minutes=10))  # 2026-05-21 00:05 UTC
        rolled = self.agg.roll_day()
        self.assertTrue(rolled)
        p = self.agg.project
        self.assertAlmostEqual(p.today_cost, 0.0)
        # Yesterday's events stay inside the week + 30d windows.
        self.assertAlmostEqual(p.week_cost, 0.30)
        self.assertAlmostEqual(p.last_30d_cost, 0.30)
        self.assertAlmostEqual(p.lifetime_cost, 0.30)

        # A fresh event after rollover lands only in today again.
        self.agg.apply_event(_ev(t=self.clock(), cost=0.05))
        p = self.agg.project
        self.assertAlmostEqual(p.today_cost, 0.05)
        self.assertAlmostEqual(p.week_cost, 0.35)

    def test_roll_day_idempotent_when_same_day(self) -> None:
        self.agg.apply_event(_ev(t=self.clock(), cost=0.10))
        self.assertFalse(self.agg.roll_day())
        self.assertAlmostEqual(self.agg.project.today_cost, 0.10)

    def test_apply_event_self_heals_across_midnight(self) -> None:
        """If the clock crosses midnight between cron ticks, ``apply_event``
        must detect the date change and reset today before adding the new
        event — otherwise today_cost includes yesterday's accumulated cost."""
        self.agg.apply_event(_ev(t=self.clock(), cost=0.40))
        self.assertAlmostEqual(self.agg.project.today_cost, 0.40)

        # Cross midnight WITHOUT a roll_day cron call.
        self.clock.advance(timedelta(minutes=10))  # 2026-05-21 00:05 UTC
        self.agg.apply_event(_ev(t=self.clock(), cost=0.05))
        # today_cost should reflect the new day only.
        self.assertAlmostEqual(self.agg.project.today_cost, 0.05)
        self.assertAlmostEqual(self.agg.project.lifetime_cost, 0.45)

    def test_30d_eviction_on_rollover(self) -> None:
        """An event that was within 30d before rollover should fall out if
        the rollover moves the 30-day floor past it."""
        # Event 31 days before "now" — already outside the 30d window.
        ancient = self.clock() - timedelta(days=31)
        self.agg.apply_event(_ev(t=ancient, cost=0.20))
        self.assertAlmostEqual(self.agg.project.last_30d_cost, 0.0)

        # Event 29 days before "now" — inside 30d.
        within = self.clock() - timedelta(days=29)
        self.agg.apply_event(_ev(t=within, cost=0.30))
        self.assertAlmostEqual(self.agg.project.last_30d_cost, 0.30)

        # Roll the clock forward 2 days → the 29-day-old event becomes 31d.
        self.clock.advance(timedelta(days=2))
        self.agg.roll_day()
        self.assertAlmostEqual(self.agg.project.last_30d_cost, 0.0)
        self.assertAlmostEqual(self.agg.project.lifetime_cost, 0.50)


class PerSliceAndSprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _FrozenClock(datetime(2026, 5, 20, 14, 0, tzinfo=UTC))
        self.agg = Aggregator(now_fn=self.clock)

    def test_per_slice_running_totals(self) -> None:
        self.agg.apply_event(_ev(t=self.clock(), slice_id="slice-4", cost=0.10))
        self.agg.apply_event(_ev(t=self.clock(), slice_id="slice-4", cost=0.20))
        self.agg.apply_event(_ev(t=self.clock(), slice_id="slice-7", cost=0.05))
        s4 = self.agg.per_slice["slice-4"]
        s7 = self.agg.per_slice["slice-7"]
        self.assertAlmostEqual(s4.cost_usd, 0.30)
        self.assertEqual(s4.turns, 2)
        self.assertAlmostEqual(s7.cost_usd, 0.05)
        self.assertEqual(s7.turns, 1)

    def test_sprint_rollup(self) -> None:
        self.agg.apply_event(
            _ev(t=self.clock(), slice_id="slice-4", cost=0.10), sprint_id="sp1"
        )
        self.agg.apply_event(
            _ev(t=self.clock(), slice_id="slice-7", cost=0.20), sprint_id="sp1"
        )
        self.agg.apply_event(
            _ev(t=self.clock(), slice_id="slice-8", cost=0.05), sprint_id="sp2"
        )
        sp1 = self.agg.per_sprint["sp1"]
        sp2 = self.agg.per_sprint["sp2"]
        self.assertAlmostEqual(sp1.cost_usd, 0.30)
        self.assertEqual(sp1.turns, 2)
        self.assertAlmostEqual(sp2.cost_usd, 0.05)

    def test_ctx_pct_is_latest_not_summed(self) -> None:
        self.agg.apply_event(_ev(t=self.clock(), ctx_pct=20))
        self.agg.apply_event(_ev(t=self.clock(), ctx_pct=73))
        self.assertEqual(self.agg.per_slice["slice-1"].ctx_pct, 73)

    def test_missing_slice_id_drops_event(self) -> None:
        bad = {"t": self.clock().isoformat(), "cost_usd": 0.10}
        self.assertIsNone(self.agg.apply_event(bad))
        self.assertAlmostEqual(self.agg.project.lifetime_cost, 0.0)


class MetricTickPayloadShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _FrozenClock(datetime(2026, 5, 20, 14, 0, tzinfo=UTC))
        self.agg = Aggregator(now_fn=self.clock)

    def test_payload_carries_slice_sprint_delta_rollups(self) -> None:
        ev = _ev(t=self.clock(), slice_id="slice-4", cost=0.41)
        payload = self.agg.apply_event(ev, sprint_id="sp1")
        assert payload is not None
        self.assertIsInstance(payload, MetricTickPayload)
        self.assertEqual(payload.slice_id, "slice-4")
        self.assertEqual(payload.sprint_id, "sp1")
        # The raw event flows through verbatim under ``delta``.
        self.assertEqual(payload.delta["cost_usd"], 0.41)
        # The rollups dict matches the slice-12 spec contract.
        r = payload.rollups
        for key in (
            "slice_cost",
            "slice_tokens_in",
            "slice_ctx_pct",
            "sprint_cost",
            "sprint_tokens",
            "project_today",
            "project_week",
            "project_30d",
            "project_lifetime",
            "project_lifetime_tokens",
        ):
            self.assertIn(key, r)
        self.assertAlmostEqual(r["slice_cost"], 0.41)
        self.assertAlmostEqual(r["project_today"], 0.41)

    def test_on_event_callback_fires(self) -> None:
        received: list[MetricTickPayload] = []
        agg = Aggregator(now_fn=self.clock, on_event=received.append)
        agg.apply_event(_ev(t=self.clock(), cost=0.10))
        agg.apply_event(_ev(t=self.clock(), cost=0.20))
        self.assertEqual(len(received), 2)
        self.assertAlmostEqual(received[1].rollups["project_lifetime"], 0.30)

    def test_callback_exception_does_not_kill_apply(self) -> None:
        def boom(_payload: MetricTickPayload) -> None:
            raise RuntimeError("downstream subscriber blew up")

        agg = Aggregator(now_fn=self.clock, on_event=boom)
        # apply_event must still update aggregates even when the subscriber dies.
        agg.apply_event(_ev(t=self.clock(), cost=0.10))
        self.assertAlmostEqual(agg.project.lifetime_cost, 0.10)


class ReplayTests(unittest.TestCase):
    """Cold-start replay: lifetime totals from incremental apply must equal
    the totals from replay-from-scratch over the same JSONL."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-agg-")).resolve()
        self.clock = _FrozenClock(datetime(2026, 5, 20, 14, 0, tzinfo=UTC))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_jsonl(self, path: Path, events: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

    def test_replay_matches_incremental(self) -> None:
        events = [
            _ev(t=self.clock() - timedelta(days=5), cost=0.10, turn=1),
            _ev(t=self.clock() - timedelta(days=2), cost=0.30, turn=2),
            _ev(t=self.clock(), cost=0.25, turn=3),
        ]
        path = self._tmp / "slice-4.tokens.jsonl"
        self._write_jsonl(path, events)

        incremental = Aggregator(now_fn=self.clock)
        for ev in events:
            incremental.apply_event(ev, sprint_id="sp1")

        replayed = Aggregator(now_fn=self.clock)
        applied = replayed.replay_jsonl(path, sprint_id="sp1")
        self.assertEqual(applied, 3)
        self.assertEqual(incremental.snapshot(), replayed.snapshot())

    def test_replay_tolerates_malformed_line(self) -> None:
        path = self._tmp / "slice-1.tokens.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(_ev(t=self.clock(), cost=0.10)) + "\n")
            f.write("not even json\n")
            f.write(json.dumps(_ev(t=self.clock(), cost=0.20)) + "\n")

        agg = Aggregator(now_fn=self.clock)
        applied = agg.replay_jsonl(path)
        self.assertEqual(applied, 2)
        self.assertAlmostEqual(agg.project.lifetime_cost, 0.30)

    def test_replay_missing_file_returns_zero(self) -> None:
        agg = Aggregator(now_fn=self.clock)
        self.assertEqual(agg.replay_jsonl(self._tmp / "absent.jsonl"), 0)


class ProjectViewSnapshotAndResetTests(unittest.TestCase):
    """Slice-12 reset semantics. Slice-10's ``ResetTests`` exercises the
    legacy nested-shape behaviour; this class confirms the ``project``
    flat-field view zeroes out on reset too."""

    def setUp(self) -> None:
        self.clock = _FrozenClock(datetime(2026, 5, 20, 14, 0, tzinfo=UTC))
        self.agg = Aggregator(now_fn=self.clock)
        self.agg.apply_event(
            _ev(t=self.clock(), slice_id="slice-1", cost=0.10), sprint_id="sp1"
        )

    def test_snapshot_shape(self) -> None:
        snap = self.agg.snapshot()
        self.assertIn("per_slice", snap)
        self.assertIn("per_sprint", snap)
        self.assertIn("per_project", snap)
        self.assertIn("slice-1", snap["per_slice"])
        self.assertIn("sp1", snap["per_sprint"])
        self.assertEqual(snap["per_slice"]["slice-1"]["sprint_id"], "sp1")

    def test_reset_clears_everything(self) -> None:
        self.agg.reset()
        self.assertEqual(self.agg.snapshot()["per_slice"], {})
        self.assertEqual(self.agg.snapshot()["per_sprint"], {})
        self.assertAlmostEqual(self.agg.project.lifetime_cost, 0.0)


if __name__ == "__main__":
    unittest.main()
