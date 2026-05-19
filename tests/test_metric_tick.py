"""Tests for naml.metric_tick — debounced emitter + daily rollover loop.

The debounced emitter is what protects the SSE channel from a bursty Claude
session. Each test drives the asyncio loop directly so timings are
deterministic.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from naml.aggregator import Aggregator, MetricTickPayload
from naml.metric_tick import (
    DEFAULT_DEBOUNCE_MS,
    DebouncedEmitter,
    daily_rollover_loop,
)


UTC = timezone.utc


class _FakePublish:
    """Records every (event_type, data) call. Async to match the production
    ``Broadcaster.publish`` signature."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, event_type: str, data: dict[str, Any]) -> None:
        self.calls.append((event_type, data))


def _payload(slice_id: str, cost: float) -> MetricTickPayload:
    return MetricTickPayload(
        slice_id=slice_id,
        sprint_id="sp1",
        delta={"slice": slice_id, "cost_usd": cost, "t": "2026-05-20T14:00:00+00:00"},
        rollups={
            "slice_cost": cost,
            "slice_tokens_in": 0,
            "slice_tokens_out": 0,
            "slice_cache_read": 0,
            "slice_cache_write": 0,
            "slice_ctx_pct": 25,
            "sprint_cost": cost,
            "sprint_tokens": 0,
            "project_today": cost,
            "project_today_tokens": 0,
            "project_week": cost,
            "project_week_tokens": 0,
            "project_30d": cost,
            "project_30d_tokens": 0,
            "project_lifetime": cost,
            "project_lifetime_tokens": 0,
        },
    )


class DebouncedEmitterTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_event_emits_after_debounce(self) -> None:
        publish = _FakePublish()
        emitter = DebouncedEmitter(publish, debounce_ms=10)
        emitter.submit(_payload("slice-1", 0.10))
        # Wait long enough for the debounce window to fire.
        await asyncio.sleep(0.05)
        self.assertEqual(len(publish.calls), 1)
        event, data = publish.calls[0]
        self.assertEqual(event, "metric-tick")
        self.assertEqual(data["slice"], "slice-1")
        self.assertAlmostEqual(data["rollups"]["slice_cost"], 0.10)

    async def test_burst_coalesces_to_latest_payload(self) -> None:
        publish = _FakePublish()
        emitter = DebouncedEmitter(publish, debounce_ms=20)
        emitter.submit(_payload("slice-1", 0.10))
        emitter.submit(_payload("slice-1", 0.20))
        emitter.submit(_payload("slice-1", 0.30))
        await asyncio.sleep(0.05)
        # Trailing-edge debounce: one publish carrying the latest value.
        self.assertEqual(len(publish.calls), 1)
        _, data = publish.calls[0]
        self.assertAlmostEqual(data["rollups"]["slice_cost"], 0.30)

    async def test_different_slices_emit_independently(self) -> None:
        publish = _FakePublish()
        emitter = DebouncedEmitter(publish, debounce_ms=10)
        emitter.submit(_payload("slice-1", 0.10))
        emitter.submit(_payload("slice-2", 0.05))
        await asyncio.sleep(0.05)
        slices = sorted(data["slice"] for _, data in publish.calls)
        self.assertEqual(slices, ["slice-1", "slice-2"])

    async def test_zero_debounce_publishes_immediately(self) -> None:
        publish = _FakePublish()
        emitter = DebouncedEmitter(publish, debounce_ms=0)
        emitter.submit(_payload("slice-1", 0.10))
        # Yield once so the scheduled task can run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(len(publish.calls), 1)

    async def test_publish_exception_does_not_break_emitter(self) -> None:
        class _Boom:
            calls = 0

            async def __call__(self, _event: str, _data: dict[str, Any]) -> None:
                _Boom.calls += 1
                raise RuntimeError("network blip")

        emitter = DebouncedEmitter(_Boom(), debounce_ms=5)
        emitter.submit(_payload("slice-1", 0.10))
        await asyncio.sleep(0.03)
        # Submit again — the emitter must not be permanently broken.
        emitter.submit(_payload("slice-1", 0.20))
        await asyncio.sleep(0.03)
        self.assertEqual(_Boom.calls, 2)

    async def test_flush_drains_inflight(self) -> None:
        publish = _FakePublish()
        emitter = DebouncedEmitter(publish, debounce_ms=30)
        emitter.submit(_payload("slice-1", 0.10))
        emitter.submit(_payload("slice-2", 0.20))
        await emitter.flush()
        self.assertEqual(len(publish.calls), 2)

    async def test_debounce_ms_must_be_non_negative(self) -> None:
        with self.assertRaises(ValueError):
            DebouncedEmitter(_FakePublish(), debounce_ms=-1)

    async def test_pending_slices_visible_during_burst(self) -> None:
        publish = _FakePublish()
        emitter = DebouncedEmitter(publish, debounce_ms=30)
        emitter.submit(_payload("slice-1", 0.10))
        emitter.submit(_payload("slice-2", 0.20))
        # Before the debounce window elapses, both should be pending.
        self.assertEqual(emitter.pending_slices, {"slice-1", "slice-2"})
        await asyncio.sleep(0.05)
        self.assertEqual(emitter.pending_slices, set())


class _FrozenClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class DailyRolloverLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_roll_day_triggered_on_date_change(self) -> None:
        clock = _FrozenClock(datetime(2026, 5, 20, 23, 59, tzinfo=UTC))
        agg = Aggregator(now_fn=clock)

        # An event before midnight.
        agg.apply_event(
            {
                "t": clock().isoformat(),
                "slice": "slice-1",
                "cost_usd": 0.40,
                "tokens_in": 100,
                "tokens_out": 0,
                "cache_read": 0,
                "cache_write": 0,
                "ctx_pct": 10,
            }
        )
        self.assertAlmostEqual(agg.project.today_cost, 0.40)

        # Run the loop with a 5ms cadence; cross midnight after one iteration.
        stop = asyncio.Event()

        async def driver() -> None:
            # Let the first tick run (no rollover yet).
            await asyncio.sleep(0.01)
            clock.advance(timedelta(minutes=2))  # 00:01 the next day
            # Let the second tick fire and detect the date change.
            await asyncio.sleep(0.02)
            stop.set()

        loop_task = asyncio.create_task(
            daily_rollover_loop(agg, interval_seconds=0.005, stop_event=stop)
        )
        await driver()
        await loop_task
        # today_cost reset, lifetime/week preserved.
        self.assertAlmostEqual(agg.project.today_cost, 0.0)
        self.assertAlmostEqual(agg.project.lifetime_cost, 0.40)
        self.assertAlmostEqual(agg.project.week_cost, 0.40)

    async def test_loop_survives_roll_day_exception(self) -> None:
        clock = _FrozenClock(datetime(2026, 5, 20, 12, 0, tzinfo=UTC))
        agg = Aggregator(now_fn=clock)

        # Patch roll_day to raise on first call, succeed after.
        call_count = {"n": 0}
        original = agg.roll_day

        def flaky() -> bool:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("transient")
            return original()

        agg.roll_day = flaky  # type: ignore[method-assign]

        stop = asyncio.Event()
        task = asyncio.create_task(
            daily_rollover_loop(agg, interval_seconds=0.005, stop_event=stop)
        )
        await asyncio.sleep(0.03)
        stop.set()
        await task
        self.assertGreaterEqual(call_count["n"], 2)

    async def test_loop_cancellation_clean(self) -> None:
        clock = _FrozenClock(datetime(2026, 5, 20, 12, 0, tzinfo=UTC))
        agg = Aggregator(now_fn=clock)
        task = asyncio.create_task(
            daily_rollover_loop(agg, interval_seconds=0.05)
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_invalid_interval_raises(self) -> None:
        agg = Aggregator()
        with self.assertRaises(ValueError):
            await daily_rollover_loop(agg, interval_seconds=0)


class DefaultsTests(unittest.TestCase):
    def test_default_debounce_is_100ms(self) -> None:
        self.assertEqual(DEFAULT_DEBOUNCE_MS, 100)


if __name__ == "__main__":
    unittest.main()
