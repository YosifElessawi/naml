"""Server-Sent Events (SSE) broadcaster for the V2 cockpit.

Powers ``GET /events`` (slice-11). One :class:`Broadcaster` instance lives on
the aiohttp ``Application`` and fans events out to every connected client.

Design choices:

- **Monotonic integer IDs.** Each broadcast event gets the next sequence
  value. The id is written on the wire so the browser-native ``EventSource``
  can send it back as ``Last-Event-ID`` after a reconnect.
- **Bounded ring buffer for replay.** The last ``history_limit`` events are
  retained. ``Last-Event-ID`` is honoured only if it can be satisfied from
  the buffer; older ids fall back to a fresh snapshot. The buffer is small
  (~256) because the cockpit is local-only and reconnect windows are short.
- **Per-client async queue with a hard cap.** A slow tab cannot pin server
  memory: if a queue grows past ``queue_limit`` it is dropped (a sentinel
  is delivered so the handler can finalise and close). This is the
  "don't block on slow clients" guarantee from slice-11.md.
- **No background tasks live here.** Heartbeat + file-watcher tasks live in
  :mod:`naml.server` and push to the broadcaster. Keeping this module pure
  makes it trivially unit-testable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any


log = logging.getLogger("naml.sse")


# Sentinel pushed into a queue when the client is dropped for being too
# slow. The /events handler unwraps the queue items and treats ``None``
# as a signal to finalise the response.
_OVERFLOW: Any = None


@dataclass(frozen=True)
class SSEEvent:
    """One server-sent event.

    ``id`` is the monotonic sequence value, ``event`` is the SSE event name
    (``snapshot`` | ``state-update`` | ``ping``), ``data`` is the JSON body.
    """

    id: int
    event: str
    data: Any


def encode_event(ev: SSEEvent) -> bytes:
    """Serialise an :class:`SSEEvent` to the SSE wire format.

    The browser ``EventSource`` parses ``id:`` / ``event:`` / ``data:``
    fields delimited by a blank line. Multi-line ``data`` values are
    flattened to a single ``data:`` field — we always serialise as JSON
    (no embedded newlines after ``json.dumps`` with default options).
    """
    body = (
        f"id: {ev.id}\n"
        f"event: {ev.event}\n"
        f"data: {json.dumps(ev.data, separators=(',', ':'))}\n\n"
    )
    return body.encode("utf-8")


class Broadcaster:
    """Fan-out hub for SSE events.

    Thread-safety: the broadcaster is asyncio-only. All callers must invoke
    methods from the server's event loop. ``publish`` is sync to keep call
    sites simple — it only manipulates in-memory structures.
    """

    def __init__(
        self,
        *,
        history_limit: int = 256,
        queue_limit: int = 64,
    ) -> None:
        self._seq = 0
        self._history: deque[SSEEvent] = deque(maxlen=history_limit)
        self._clients: set[asyncio.Queue[SSEEvent | None]] = set()
        self._queue_limit = queue_limit

    # --- introspection ------------------------------------------------

    @property
    def current_id(self) -> int:
        """The id of the most recent published event (0 if none yet)."""
        return self._seq

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # --- mutation -----------------------------------------------------

    def publish(self, event: str, data: Any) -> SSEEvent:
        """Append a new event, store it in history, broadcast to all clients."""
        self._seq += 1
        ev = SSEEvent(id=self._seq, event=event, data=data)
        self._history.append(ev)
        self._broadcast(ev)
        return ev

    def _broadcast(self, ev: SSEEvent) -> None:
        # Snapshot the set so a queue removed mid-iteration doesn't trip us.
        for q in list(self._clients):
            if q.qsize() >= self._queue_limit:
                # Slow client: drop it. The handler reads the sentinel and
                # closes its response.
                self._clients.discard(q)
                try:
                    q.put_nowait(_OVERFLOW)
                except asyncio.QueueFull:  # pragma: no cover — defence
                    pass
                log.warning("dropping slow SSE client (queue full)")
                continue
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:  # pragma: no cover — bounded already
                self._clients.discard(q)

    # --- subscription -------------------------------------------------

    def register(
        self, *, last_event_id: int | None = None,
    ) -> tuple[asyncio.Queue[SSEEvent | None], list[SSEEvent]]:
        """Register a new client.

        Returns ``(queue, replay)`` where ``replay`` is the list of buffered
        events with id strictly greater than ``last_event_id`` — empty if
        ``last_event_id`` is ``None`` or cannot be satisfied from history.
        """
        q: asyncio.Queue[SSEEvent | None] = asyncio.Queue()
        self._clients.add(q)

        replay: list[SSEEvent] = []
        if last_event_id is not None and self._history:
            # Only honour Last-Event-ID if our buffer still covers it. We
            # define "covers" as: the oldest retained id is <= last+1 (the
            # next id we would want to send). If the buffer has rolled past
            # that point, fall back to a fresh snapshot.
            oldest = self._history[0].id
            if oldest <= last_event_id + 1:
                replay = [ev for ev in self._history if ev.id > last_event_id]

        return q, replay

    def unregister(self, q: asyncio.Queue[SSEEvent | None]) -> None:
        self._clients.discard(q)
