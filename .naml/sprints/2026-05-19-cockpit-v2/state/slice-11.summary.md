# slice-11 — SSE endpoint + state-update events + EventSource client

## What changed
- **`naml/sse.py`** (new) — `Broadcaster` with monotonic event ids, bounded
  ring-buffer history for `Last-Event-ID` replay, and a per-client async
  queue with a hard cap so a slow tab gets dropped instead of pinning
  server memory. `encode_event` emits the SSE wire format
  (`id:` / `event:` / `data:`).
- **`naml/server.py`** — adds `GET /events` (SSE), a 2-second heartbeat
  task, and a 50ms mtime-poll state watcher task. Both background tasks
  spin up in `on_startup` and unwind in `on_cleanup`. `build_app` now
  accepts `aggregator=`, `broadcaster=`, `heartbeat_interval=`, and
  `state_watch_interval=` kwargs for tests + slice-10 integration. Switched
  app dict accesses to `web.AppKey` instances (silences aiohttp's
  `NotAppKeyWarning`). `/state` placeholder is preserved for `naml status`.
- **`web/src/sync/EventSource.ts`** (new) — `NamlEventSource` wrapper around
  native `EventSource`. Handles `snapshot`, `state-update`, `ping`. Derives
  `connecting → connected → slow → lost` from time-since-last-event using a
  configurable sync tick. Auto-reconnect when the page becomes visible
  after a `lost` window. `EventSourceCtor` is injectable for tests.
- **`web/src/store/`** (new) — tiny pub/sub store, no Redux. `subscribe` for
  all-state listeners; `subscribeKey<K>` for fine-grained re-renders (the
  header sync dot can pin to just `syncStatus`). `applySnapshot` /
  `applyStateUpdate` / `applyPing` / `setSyncStatus` are the only mutators.
  A module-level singleton `store` is exported for shared use.
- **`tests/test_sse.py`** (new) — 12 tests covering broadcaster unit
  behaviour (monotonic ids, replay, slow-client drop, encoding) plus
  end-to-end SSE flow against the real aiohttp app (snapshot first, ping
  cadence, state-update under 1s, `Last-Event-ID` replay,
  garbage-id fallback).
- **`web/src/sync/EventSource.test.ts`** + **`web/src/store/store.test.ts`**
  (new) — 13 vitest tests with a mock EventSource. Verify snapshot apply,
  delta merge, `slow`/`lost` transitions, `forceReconnect`, malformed
  payload tolerance, key-subscriber notifications.
- **`web/vite.config.ts` + `web/tsconfig.json`** — single-line `@ts-expect-error`
  on the `test:` block to work around vitest 2.1 bundling vite 5 types
  while the project ships vite 6. Pre-existing fault on `main` that
  broke `pnpm typecheck`; resolved here because slice-11 was the first
  slice with vitest tests that actually exercised the config.

## Non-obvious decisions
- **Polling, not `watchdog`, for state files.** Slice-10 introduces
  `watchdog` for the tokens JSONL; state JSON files mutate at human pace
  so a 50ms `stat()` poll is well under the 200ms latency target and adds
  zero new runtime deps to this slice. Slice-10's `Aggregator` is wired
  through `aggregator=` kwarg on `build_app` for the snapshot payload.
- **Snapshot id = current broadcaster sequence.** A snapshot reflects state
  "as of" the latest published event, so its SSE id is the broadcaster's
  current sequence. On reconnect with `Last-Event-ID` set, the server
  prefers replay-from-buffer; if the buffer can't cover the gap (oldest
  retained id > requested + 1), it falls back to a fresh snapshot.
- **First-pass watcher silence.** On startup the watcher walks state files,
  populates the mtime map, and does NOT emit. That avoids a flood of
  state-updates at every connect (the snapshot already carries the
  initial state). Subsequent iterations emit on any mtime delta.
- **Sync-status `connecting` is sticky.** The status only flips off
  `connecting` when a real event arrives — even if the ticker thinks the
  connection is "slow" by elapsed time, the absence of any event keeps
  it as `connecting`. Prevents misleading "slow" flashes before the
  first message lands.
- **Slow-client drop via sentinel.** The broadcaster pushes `None` onto a
  full client queue and removes that client from the set. The handler
  unwraps `None` and finalises the response — no per-client deadlines.
- **Aggregator integration is opt-in.** When slice-10 lands, the cockpit
  will pass its aggregator into `build_app(...)`; until then the
  snapshot's `aggregates` field is `{}` and the UI can show fixtures.

## For downstream slices
- **slice-12 (UI)** consumes `web/src/sync/EventSource.ts` + `web/src/store/`.
  Subscribe the header's sync dot with `store.subscribeKey("syncStatus", …)`;
  dashboard / sprint pane should subscribe to the whole store and re-render
  on snapshot/state-update. Call `startEventSource(store)` once during app
  bootstrap (probably in `main.tsx`).
- **slice-2 / merge** — the existing header (`web/src/components/Header/`)
  has a static `syncState` prop. Once slice-2 + slice-11 are merged, plug
  the prop into `useSyncStatus()` (see store) so the dot is live.
- **Aggregator wiring** (slice-10 / slice-13) — pass the aggregator into
  `server.build_app(..., aggregator=agg)`. The snapshot will then include
  `aggregates` under the per-project shape from slice-10's `snapshot()`.
- **Event-id contract** — IDs are integers, strictly monotonic per server
  process. `Last-Event-ID` is honoured if the ring buffer (last 256
  events) still covers the gap. Don't rely on cross-restart id stability.
- **`/state` placeholder still exists** for backwards compat with
  `naml status`. Real telemetry rides `/events`.
