# slice-11: SSE endpoint + state-update events + EventSource client

## What to build

The transport that wires the Python server's state knowledge to the browser
in real time. Server-Sent Events (SSE), driven by the same file watcher
from slice-10.

### Python side

1. **`/events` SSE endpoint** in `naml/server.py`. Long-lived response with
   `Content-Type: text/event-stream`.
2. **On connect**: emit a full snapshot event:
   ```
   event: snapshot
   data: { all current sprint states + slice states + aggregator snapshot }
   ```
3. **Heartbeat**: every 2 seconds emit a `ping` event:
   ```
   event: ping
   data: { "t": "<iso8601>" }
   ```
4. **State-update events**: when the watcher detects a change to a
   `state/<slice-or-sprint>.status.json` (existing files from slice-9 + Phase 5),
   parse the JSON and emit:
   ```
   event: state-update
   data: { "kind": "slice" | "sprint", "id": "<id>", "delta": {...} }
   ```
5. **Connection management**: hold a set of active client queues; broadcast
   each event to all queues. On client disconnect, drop the queue. Support
   `Last-Event-ID` header for catch-up.

### Browser side

1. **`web/src/sync/EventSource.ts`** — wrapper around browser `EventSource`:
   - Auto-reconnect with exponential backoff (built-in to EventSource)
   - Handles `snapshot` on connect, `state-update` for deltas, `ping` for liveness
   - Exposes a `connected` / `slow` / `lost` / `connecting` state derived
     from time-since-last-event (5s → slow, 15s → lost)
2. **`web/src/store/`** — central store (plain object + listeners, no Redux):
   - `sprints: Record<id, SprintState>`
   - `slices: Record<id, SliceState>`
   - `aggregates: ProjectMetrics`
   - `syncStatus: 'live' | 'slow' | 'lost' | 'connecting'`
   - Subscribers (`store.subscribe(key, cb)`) for fine-grained re-renders
3. **Wire the header sync dot** (built in slice-2) to `syncStatus`. The dot
   shows the 4 visual variants per Q8 spec (color + pulse cadence).

### Tests

- Python: `tests/test_sse.py` — connect to `/events`, verify snapshot + ping
  cadence + state-update emission after a state file write
- Browser: `web/src/sync/EventSource.test.ts` — mock EventSource, verify
  the store gets patched on synthetic events

## Acceptance criteria

- [ ] `GET /events` returns an SSE stream with snapshot as first event
- [ ] Heartbeat fires every 2 seconds
- [ ] State file mutation → SSE event in < 200ms
- [ ] Browser sync dot reflects the connection state
- [ ] Reconnect after server restart works without page reload
- [ ] `Last-Event-ID` is honored — server replays events since that ID
- [ ] No metric data yet (cost timeline shows fixtures until slice-12)
- [ ] All gates pass

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q8-sync.html

## Constraints

- **SSE only**, no WebSocket. Q8 decision.
- **2s heartbeat**, **5s SLOW threshold**, **15s LOST threshold**. Q8 decision.
- **EventSource is browser-native** — no library wrappers, no polyfills.
- **Server must not block on slow clients**: if a client queue grows past
  N events, drop the connection. Otherwise one stalled tab pins memory.

## Notes from grilling session

The sync dot is the cockpit's heartbeat indicator. It can never lie — if the
dot says LIVE but the cockpit is showing stale data, the user's mental model
breaks. Be conservative with the state transitions (SLOW at 5s is generous,
LOST at 15s is patient).

Snapshot on connect is what makes "refresh the page mid-sprint" feel
instant — the user lands and everything's already there. Test this end-to-end
with a real running sprint.
