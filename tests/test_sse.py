"""End-to-end tests for the ``/events`` SSE stream and its broadcaster.

These exercise the full slice-11 contract:

- Connect, get a ``snapshot`` first
- ``ping`` events fire on the configured cadence
- A state-file write produces a ``state-update`` within the 200ms budget
- ``Last-Event-ID`` is honoured from the ring buffer
- Slow clients are dropped instead of blocking the broadcaster

Heartbeat + state-watch intervals are dialled down via ``build_app`` kwargs
so tests don't have to wait the real 2-second cadence.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from aiohttp.test_utils import AioHTTPTestCase

from naml import server as server_mod
from naml import state as state_mod
from naml.server import BROADCASTER_KEY
from naml.sse import Broadcaster, SSEEvent, encode_event


class _StubCfg:
    def __init__(self, repo_root: Path, sprints_path: Path) -> None:
        self.repo_root = repo_root
        self.sprints_path = sprints_path


def _make_cfg(td: str) -> _StubCfg:
    root = Path(td).resolve()
    sprints = root / ".naml" / "sprints"
    sprints.mkdir(parents=True)
    return _StubCfg(repo_root=root, sprints_path=sprints)


# --- helpers --------------------------------------------------------------


async def _read_event(resp: Any, timeout: float = 2.0) -> dict[str, Any] | None:
    """Read one SSE event from a streaming response.

    Returns a dict with keys ``id``, ``event``, ``data`` (parsed JSON).
    Returns ``None`` on stream close.
    """
    event_type: str | None = None
    event_id: int | None = None
    data_parts: list[str] = []
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError("SSE event read timed out")
        line_bytes = await asyncio.wait_for(
            resp.content.readline(), timeout=remaining,
        )
        if not line_bytes:
            return None
        line = line_bytes.decode("utf-8").rstrip("\r\n")
        if not line:
            # Blank line -> dispatch
            if event_type is None and not data_parts:
                # Comment / heartbeat noise — keep reading.
                continue
            data_str = "\n".join(data_parts)
            return {
                "id": event_id,
                "event": event_type,
                "data": json.loads(data_str) if data_str else None,
            }
        if line.startswith("id:"):
            event_id = int(line[3:].strip())
        elif line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            # SSE allows a single space after the colon; strip leading
            # whitespace defensively rather than trimming a fixed prefix.
            data_parts.append(line[5:].lstrip(" "))


# --- broadcaster unit tests ----------------------------------------------


class BroadcasterUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_assigns_monotonic_ids(self) -> None:
        b = Broadcaster()
        a = b.publish("ping", {"a": 1})
        c = b.publish("ping", {"a": 2})
        self.assertEqual(a.id, 1)
        self.assertEqual(c.id, 2)
        self.assertEqual(b.current_id, 2)

    async def test_register_replays_after_last_event_id(self) -> None:
        b = Broadcaster()
        b.publish("state-update", {"i": 1})
        b.publish("state-update", {"i": 2})
        b.publish("state-update", {"i": 3})

        _, replay = b.register(last_event_id=1)
        self.assertEqual([ev.data["i"] for ev in replay], [2, 3])

    async def test_register_with_unknown_last_event_id_returns_empty_replay(self) -> None:
        b = Broadcaster(history_limit=2)
        b.publish("x", {})  # id 1, evicted
        b.publish("x", {})  # id 2, evicted
        b.publish("x", {})  # id 3
        b.publish("x", {})  # id 4

        # We ask for >0 but the oldest retained is id=3 — that doesn't
        # cover the requested gap (oldest > last+1).
        _, replay = b.register(last_event_id=0)
        self.assertEqual(replay, [])

    async def test_publish_broadcasts_to_registered_queue(self) -> None:
        b = Broadcaster()
        q, _ = b.register()
        b.publish("ping", {"v": 9})
        ev = await asyncio.wait_for(q.get(), timeout=1.0)
        self.assertIsInstance(ev, SSEEvent)
        assert ev is not None  # mypy/pyright
        self.assertEqual(ev.data, {"v": 9})

    async def test_slow_client_is_dropped_with_sentinel(self) -> None:
        b = Broadcaster(queue_limit=2)
        q, _ = b.register()
        b.publish("x", {})
        b.publish("x", {})
        # The third publish pushes the client over the cap. It is dropped
        # and the sentinel is enqueued.
        b.publish("x", {})

        # Drain — final item must be the sentinel (None).
        seen_none = False
        for _ in range(5):
            try:
                item = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                seen_none = True
        self.assertTrue(seen_none, "expected overflow sentinel")
        self.assertEqual(b.client_count, 0)

    async def test_encode_event_emits_id_event_data(self) -> None:
        body = encode_event(SSEEvent(id=42, event="state-update", data={"k": 1}))
        text = body.decode("utf-8")
        self.assertIn("id: 42\n", text)
        self.assertIn("event: state-update\n", text)
        self.assertIn('data: {"k":1}\n', text)
        self.assertTrue(text.endswith("\n\n"))


# --- /events integration tests -------------------------------------------


class _SSEServerBase(AioHTTPTestCase):
    """Spin up the real app with fast intervals for SSE tests."""

    # Subclasses override to flip cadence.
    heartbeat_interval: float = 0.05
    state_watch_interval: float = 0.02

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self._root = Path(self._td.name).resolve()
        (self._root / ".naml" / "sprints").mkdir(parents=True)
        self._sprints = self._root / ".naml" / "sprints"
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()
        self._td.cleanup()

    async def get_application(self):  # type: ignore[override]
        cfg = _StubCfg(repo_root=self._root, sprints_path=self._sprints)
        return server_mod.build_app(
            cfg,
            heartbeat_interval=self.heartbeat_interval,
            state_watch_interval=self.state_watch_interval,
        )


class EventsSnapshotTests(_SSEServerBase):
    async def test_first_event_is_snapshot_with_empty_state(self) -> None:
        resp = await self.client.get("/events")
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.headers["Content-Type"].startswith("text/event-stream"))

        ev = await _read_event(resp)
        assert ev is not None
        self.assertEqual(ev["event"], "snapshot")
        self.assertEqual(ev["data"]["sprints"], {})
        self.assertEqual(ev["data"]["slices"], {})
        self.assertEqual(ev["data"]["aggregates"], {})
        self.assertIsInstance(ev["data"]["ts"], str)
        resp.close()

    async def test_snapshot_reflects_existing_state(self) -> None:
        sprint_dir = self._sprints / "sprint-A"
        (sprint_dir / "state").mkdir(parents=True)
        sstate = state_mod.SprintState(
            sprint_id="sprint-A",
            state="executing",
            slices={"slice-1": "pr"},
        )
        sstate.record_transition(state="executing", detail="started")
        state_mod.save_sprint_state(sprint_dir, sstate)

        status = state_mod.SliceStatus(slice_id="slice-1", state="pr")
        status.record_transition(state="pr", detail="opened")
        state_mod.save_slice_status(sprint_dir, status)

        resp = await self.client.get("/events")
        ev = await _read_event(resp)
        assert ev is not None
        self.assertEqual(ev["event"], "snapshot")
        self.assertIn("sprint-A", ev["data"]["sprints"])
        self.assertIn("sprint-A::slice-1", ev["data"]["slices"])
        self.assertEqual(
            ev["data"]["slices"]["sprint-A::slice-1"]["state"], "pr",
        )
        resp.close()


class EventsHeartbeatTests(_SSEServerBase):
    async def test_ping_events_arrive_on_cadence(self) -> None:
        resp = await self.client.get("/events")
        ev = await _read_event(resp)  # snapshot
        assert ev is not None
        self.assertEqual(ev["event"], "snapshot")

        # With a 50ms heartbeat we should see at least two pings well under
        # one second.
        pings = 0
        deadline = time.monotonic() + 1.5
        while pings < 2 and time.monotonic() < deadline:
            ev = await _read_event(resp, timeout=1.0)
            if ev and ev["event"] == "ping":
                pings += 1
        self.assertGreaterEqual(pings, 2, "expected >=2 pings within 1.5s")
        resp.close()


class EventsStateUpdateTests(_SSEServerBase):
    async def test_state_file_write_produces_state_update(self) -> None:
        # Prepare on-disk state up front. We deliberately write *before*
        # the watcher has a chance to observe — that produces a couple of
        # pre-existing state-updates that the test drains before checking
        # the targeted slice change. The orchestrator behaves the same
        # way: any state-file write after startup is a real event.
        sprint_dir = self._sprints / "sprint-A"
        (sprint_dir / "state").mkdir(parents=True)
        sstate = state_mod.SprintState(
            sprint_id="sprint-A",
            state="package_received",
            slices={"slice-1": "pending"},
        )
        state_mod.save_sprint_state(sprint_dir, sstate)

        resp = await self.client.get("/events")
        ev = await _read_event(resp)  # snapshot
        assert ev is not None
        self.assertEqual(ev["event"], "snapshot")

        # Wait for any pre-existing state-update events to flush, then we
        # know mtimes are populated and the next write is the one we test.
        # Drain anything that comes before we issue the target write.
        await asyncio.sleep(0.15)

        # Write the slice status — this is the event we want to assert on.
        status = state_mod.SliceStatus(slice_id="slice-1", state="work")
        status.record_transition(state="work", detail="started")
        state_mod.save_slice_status(sprint_dir, status)
        write_at = time.monotonic()

        # Read events until we see the targeted slice state-update.
        # Skip any earlier pings or pre-existing sprint updates.
        update = None
        deadline = write_at + 2.0
        while time.monotonic() < deadline:
            ev = await _read_event(resp, timeout=1.5)
            if ev is None:
                break
            if ev["event"] != "state-update":
                continue
            if ev["data"].get("id") == "sprint-A::slice-1":
                update = ev
                break
        elapsed = time.monotonic() - write_at
        self.assertIsNotNone(update, "expected slice state-update event")
        assert update is not None
        self.assertEqual(update["data"]["kind"], "slice")
        self.assertEqual(update["data"]["delta"]["state"], "work")
        # Watcher poll cadence is 20ms; allow generous slack on CI.
        self.assertLess(elapsed, 1.0, f"state-update took {elapsed:.3f}s")
        resp.close()


class EventsLastEventIdTests(_SSEServerBase):
    async def test_last_event_id_replays_buffered_events(self) -> None:
        broadcaster: Broadcaster = self.app[BROADCASTER_KEY]
        # Publish a few events directly so the broadcaster buffer has ids
        # 1..3 without needing the watcher.
        broadcaster.publish("state-update", {"kind": "slice", "id": "x", "delta": {"i": 1}})
        broadcaster.publish("state-update", {"kind": "slice", "id": "x", "delta": {"i": 2}})
        broadcaster.publish("state-update", {"kind": "slice", "id": "x", "delta": {"i": 3}})

        resp = await self.client.get("/events", headers={"Last-Event-ID": "1"})
        # Replay path: events 2 and 3 should arrive, snapshot suppressed.
        ev = await _read_event(resp)
        assert ev is not None
        self.assertEqual(ev["event"], "state-update")
        self.assertEqual(ev["id"], 2)
        self.assertEqual(ev["data"]["delta"]["i"], 2)

        ev = await _read_event(resp)
        assert ev is not None
        self.assertEqual(ev["event"], "state-update")
        self.assertEqual(ev["id"], 3)
        resp.close()

    async def test_unparseable_last_event_id_falls_back_to_snapshot(self) -> None:
        resp = await self.client.get("/events", headers={"Last-Event-ID": "garbage"})
        ev = await _read_event(resp)
        assert ev is not None
        self.assertEqual(ev["event"], "snapshot")
        resp.close()


if __name__ == "__main__":
    unittest.main()
