# slice-10 — in-memory aggregator + file watcher + cold-start replay

## What changed
- **`naml/aggregator.py`** (new) — `Aggregator` with `per_slice`, `per_sprint`,
  `per_project` (today / this_week / last_30d / lifetime). `apply_event` is
  O(1). `snapshot()` returns the shape SSE consumers and slice-12 will read.
  `reset()` zeroes state for a re-replay. UTC daily buckets everywhere
  (DST-safe).
- **`naml/watcher.py`** (new) — `replay_all` does the cold-start scan and
  returns `(count, positions)`. `TokensWatcher` runs a `watchdog` Observer
  with a 50ms debounced drain. `_read_new_lines` returns only newline-
  terminated records and advances the byte cursor; partial lines stay on
  disk for the next drain. Positions persist to `.naml/state-cursor.json`.
- **`naml/server.py`** — `build_app` now constructs (or accepts) an
  `Aggregator`, runs cold-start replay, and stores both the aggregator
  and the sprints root on the aiohttp `Application`. New routes:
  `GET /aggregates` (snapshot) and `POST /aggregates/reset` (drop + replay).
- **`pyproject.toml`** — added `watchdog>=3.0` as a runtime dep (first
  non-stdlib dep besides aiohttp).
- **Tests** — `tests/test_aggregator.py` (windows / DST / reset / perf),
  `tests/test_watcher.py` (replay / cursor / debounce / 200ms integration
  if `watchdog` installed), `tests/test_server.py` extended with
  `AggregatesEndpointTests`.

## Files
- `naml/aggregator.py` (new)
- `naml/watcher.py` (new)
- `naml/server.py`, `pyproject.toml` (modified)
- `tests/test_aggregator.py`, `tests/test_watcher.py` (new)
- `tests/test_server.py` (modified)

## Non-obvious decisions
- **Time-window math runs at `snapshot()` time**, not on apply. Apply only
  touches one daily bucket — keeps the hot path O(1). Snapshot sums at most
  30 buckets to compute `last_30d`.
- **`lifetime` is a running total** kept in `ProjectMetrics.lifetime`, NOT
  recomputed from daily buckets. This keeps lifetime O(1) and survives
  events whose `t` field is unparseable (extremely defensive but cheap).
- **`sprint_id` is derived from the file path** (`.../sprints/<sprint_id>/state/...`)
  by both the watcher and `replay_all` — JSONL events themselves don't carry
  `sprint`, so the file layout is the authority.
- **Cursor file (`.naml/state-cursor.json`) is written but cold-start still
  does a full replay**. Per the spec, the cursor's purpose is to survive a
  restart for a future "warm restart" path; for now it's operator-visible
  and persisted, but boot path is always full-scan into a fresh aggregator.
- **`watchdog` import is lazy** inside `TokensWatcher.start()` so the
  aggregator + cold-start tests stay importable even before `pip install`
  picks up the new runtime dep.
- **Debounce uses a single threading.Timer**, rescheduled-or-skipped on
  the lock — coalesces a burst of writes into one drain.

## For downstream slices
- **slice-11 (SSE)** can construct its watcher with `on_drain=` to emit a
  `metric-tick` event per drained batch. The aggregator snapshot reflects
  state *after* the drain. The watcher exposes `force_flush()` for
  testing / shutdown.
- **slice-12 (UI)** can `GET /aggregates` for the initial bootstrap and
  treat subsequent SSE patches as deltas; the snapshot shape under
  `per_project` is `{today, this_week, last_30d, lifetime, last_event_at}`,
  each with `cost_usd / tokens_in / tokens_out / cache_read / cache_write /
  turns` (lifetime also carries `started_at`).
- **Settings → Advanced → Reset Aggregates** wires to
  `POST /aggregates/reset`; response is `{status, events_replayed}`.
- The watcher recognises file moves into the watched name (atomic
  rename-into-place) so future JSONL compaction can swap a file safely.
