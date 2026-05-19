"""SSE ``metric-tick`` plumbing — debounced per-slice emitter + daily
rollover loop.

These are the moving parts that turn :class:`naml.aggregator.Aggregator`
into a live cockpit signal:

1. :class:`DebouncedEmitter` — a trailing-edge per-slice debouncer.
   ``submit(payload)`` may be called from sync code; the emitter coalesces
   bursts (< 100ms) into a single SSE publish using the latest payload.
2. :func:`daily_rollover_loop` — an async background task that calls
   ``aggregator.roll_day()`` once a minute so the ``today`` window resets at
   UTC midnight even when no events fire across the boundary.

The cockpit server (slice-11 ``naml/server.py``) is expected to construct a
``DebouncedEmitter`` once, wire it to the aggregator's ``on_event`` hook,
and spawn :func:`daily_rollover_loop` as a background task in
``on_startup`` / cancel it in ``on_cleanup``.

This module has no aiohttp / broadcaster imports — it takes any async
``publish`` callable so unit tests can drive it with a fake.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from .aggregator import Aggregator, MetricTickPayload


log = logging.getLogger("naml.metric_tick")


# Default debounce window. Spec.md Q8b + slice-12 brief: a Claude turn can
# fire every few hundred ms during a busy session; 100ms coalesces a
# transient burst into a single SSE event without losing visible latency.
DEFAULT_DEBOUNCE_MS = 100

# Daily rollover cron cadence. Once a minute is overkill for a once-per-day
# event but is cheap (one dict lookup) and avoids any drift if the loop is
# briefly paused (suspended laptop, GC pause).
DEFAULT_ROLLOVER_INTERVAL_SECONDS = 60.0


# An async callable that publishes one SSE event with a string event-name
# and a JSON-serialisable data dict. Matches slice-11's ``Broadcaster.publish``
# shape but stays duck-typed.
PublishCallable = Callable[[str, dict[str, Any]], Awaitable[None]]


class DebouncedEmitter:
    """Trailing-edge per-slice debounced emitter for ``metric-tick`` events.

    Coalesces bursts within ``debounce_ms`` of each other into a single
    publish that carries the latest payload. The first event for a slice
    schedules a timer; subsequent events within the window update the
    pending payload but don't reset the timer (trailing-edge semantics).

    Submit can be called from sync code — the emitter schedules its own
    asyncio task on the running loop. Callers must construct it inside the
    event loop's thread.
    """

    def __init__(
        self,
        publish: PublishCallable,
        *,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if debounce_ms < 0:
            raise ValueError("debounce_ms must be non-negative")
        self._publish = publish
        self._debounce_seconds = debounce_ms / 1000.0
        self._loop = loop
        self._pending: dict[str, MetricTickPayload] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def pending_slices(self) -> set[str]:
        """Read-only view of slices with a pending unflushed payload."""
        return set(self._pending)

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        return asyncio.get_event_loop()

    def submit(self, payload: MetricTickPayload) -> None:
        """Queue ``payload`` for emission. Coalesces with any pending payload
        for the same slice (latest-wins).

        Safe to call from sync code as long as an asyncio loop is running.
        """
        slice_id = payload.slice_id
        self._pending[slice_id] = payload
        if slice_id in self._tasks:
            # A timer is already in flight — it'll pick up the new payload
            # when it fires. Trailing edge, no timer reset.
            return
        loop = self._get_loop()
        self._tasks[slice_id] = loop.create_task(self._fire_after_debounce(slice_id))

    async def _fire_after_debounce(self, slice_id: str) -> None:
        try:
            if self._debounce_seconds > 0:
                await asyncio.sleep(self._debounce_seconds)
            payload = self._pending.pop(slice_id, None)
            if payload is None:
                return
            try:
                await self._publish("metric-tick", payload.to_dict())
            except Exception:  # noqa: BLE001 — never crash the emitter loop
                log.exception("metric-tick publish failed for slice %s", slice_id)
        finally:
            self._tasks.pop(slice_id, None)

    async def flush(self) -> None:
        """Await all in-flight debounce timers. Used on shutdown so a final
        ``metric-tick`` lands before the broadcaster disappears."""
        pending = list(self._tasks.values())
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


async def daily_rollover_loop(
    aggregator: Aggregator,
    *,
    interval_seconds: float = DEFAULT_ROLLOVER_INTERVAL_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Long-running async loop that calls :meth:`Aggregator.roll_day` every
    ``interval_seconds``.

    The loop survives an exception in ``roll_day`` — we'd rather lose a tick
    than crash the whole server's metrics pipeline. Cancellation propagates
    cleanly via :class:`asyncio.CancelledError`.

    ``stop_event`` is an optional async signal — when set, the loop exits at
    the next iteration. Tests use it to terminate the loop deterministically.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                aggregator.roll_day()
            except Exception:  # noqa: BLE001 — defensive; cron must not die
                log.exception("daily rollover tick failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        # Normal shutdown path. Re-raise so the task records as cancelled.
        raise


def attach(
    aggregator: Aggregator,
    publish: PublishCallable,
    *,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    rollover_interval_seconds: float = DEFAULT_ROLLOVER_INTERVAL_SECONDS,
    loop: asyncio.AbstractEventLoop | None = None,
) -> tuple[DebouncedEmitter, asyncio.Task[None]]:
    """Wire ``aggregator`` up to ``publish`` and start the rollover task.

    Convenience for slice-13's eventual server wiring; tests prefer to
    construct ``DebouncedEmitter`` and call :func:`daily_rollover_loop`
    directly so they can drive each piece independently.

    Returns the ``(emitter, rollover_task)`` pair so the caller can cancel
    the task on shutdown and ``await emitter.flush()`` if desired.
    """
    emitter = DebouncedEmitter(publish, debounce_ms=debounce_ms, loop=loop)
    aggregator.set_on_event(emitter.submit)
    loop_ref = loop or asyncio.get_event_loop()
    task = loop_ref.create_task(
        daily_rollover_loop(aggregator, interval_seconds=rollover_interval_seconds)
    )
    return emitter, task
