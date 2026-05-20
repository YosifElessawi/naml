"""Tests for naml.aggregator — the in-memory metrics rollup.

Covers slice-10's acceptance criteria for the aggregator:

- ``apply_event`` is O(1) per event (asserted by wall-clock on 10k events).
- per_slice / per_sprint / per_project totals match expected sums.
- today / this_week / last_30d / lifetime windows are computed from
  UTC daily buckets — DST-safe.
- ``reset`` clears all aggregates; the caller can re-replay.
- ``snapshot`` exposes the shape the SSE consumer (slice-12) needs.
"""

from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta, timezone

from naml.aggregator import (
    MONTH_DAYS,
    WEEK_DAYS,
    Aggregator,
    SliceMetrics,
    SprintMetrics,
    WindowTotals,
)


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
    """Build a token-event dict matching the JSONL schema in spec Q8b."""
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
    distances and assert which windows they fall into.
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


if __name__ == "__main__":
    unittest.main()
