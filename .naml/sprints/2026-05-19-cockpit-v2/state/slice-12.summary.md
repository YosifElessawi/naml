# slice-12 — metric-tick events + cost timeline rollups

## What changed
- **`naml/aggregator.py`** (new) — `Aggregator` with `apply_event`, `snapshot`,
  `reset`, `replay_jsonl`. Maintains `per_slice` / `per_sprint` / `per_project`
  rollups + the four time-window totals (today / week / 30d / lifetime) in
  UTC. Per-day buckets back the window math; `roll_day()` evicts >30d buckets
  and recomputes today/week/30d when the UTC date flips. `apply_event` also
  self-heals across midnight so an event arriving in the gap between cron
  ticks doesn't carry stale `today_cost`. Returns a `MetricTickPayload` per
  applied event with the full slice-12 rollup contract.
- **`naml/metric_tick.py`** (new) — `DebouncedEmitter` (trailing-edge per-slice
  100ms coalesce) + `daily_rollover_loop` async task (60s cadence by default).
  Both are construction-injection–friendly (publish callable, stop_event) so
  slice-11's broadcaster wiring can adopt them with one line. `attach(...)`
  one-shot wires aggregator → emitter → cron task.
- **`web/src/store/metrics.ts`** (new) — TS types matching the server contract
  + `metricTickPatch(prev, payload)` pure reducer + `applyMetricTick(store,
  payload)` for slice-11 pub/sub stores + `parseMetricTickPayload` runtime
  guard.
- **`web/src/lib/tween.ts`** (new) — rAF counter tween, 600ms ease-out
  default. Cancellable. Falls back to `setTimeout` when rAF is missing.
- Tests: `tests/test_aggregator.py` (21), `tests/test_metric_tick.py` (13),
  `web/src/store/metrics.test.ts` (13), `web/src/lib/tween.test.ts` (10).
  266 Python + 24 vitest, all green.

## Non-obvious decisions
- **Self-heal midnight.** `apply_event` calls `roll_day()` upfront so even if
  the 60s cron tick hasn't fired yet, the first event of the new day lands in
  a freshly-zeroed `today` window. Without this, the cost timeline could lie
  for up to 60 seconds at midnight UTC.
- **Trailing-edge debounce, not throttle.** A burst of three events within
  100ms collapses to ONE emit carrying the latest payload — animation is
  smoother and bandwidth is bounded. Subscribers never see a stale tick after
  a newer one.
- **Sprint tokens summed locally from delta.** The wire `sprint_tokens`
  rollup is a single total; the browser still keeps tokens_in / out / cache_*
  per-sprint for the UI's tokens-in/out breakdown. Cost stays
  server-authoritative.
- **Per-day buckets, not a deque-of-events.** O(30) memory per project, O(1)
  apply, O(30) rollover. Beats raw event replay for cold-start performance
  if a sprint has 10k+ turns (none of mine will for years).
- **Aggregator is sync.** Watcher (slice-10) is the only caller; no thread
  contention. `metric_tick.DebouncedEmitter.submit` is sync too (schedules
  its own asyncio task) so the watcher's filesystem callback chain stays
  callback-flat.

## For downstream slices
- **slice-13 (UI wiring)**: subscribe to `slice_metrics[id]`, `sprint_metrics[id]`,
  and `project_metrics` from the store. Use `tween()` from `web/src/lib/tween`
  for every cost / token counter so updates count up smoothly. Cancellation
  pattern: keep the returned `TweenCancel` per counter and call it before
  starting a fresh tween when a new value lands.
- **Server wiring** (slice-13 or future server-side merger): import
  `metric_tick.attach(aggregator, broadcaster.publish)` from `build_app(...)`
  during `on_startup`; cancel the returned rollover task in `on_cleanup`.
  Call `await emitter.flush()` before broadcaster teardown so the last
  metric-tick lands.
- **`Aggregator.on_event` callback** is sync — if you need to do async work
  in response, post to a queue from inside the callback.
- **Snapshot shape** is `{per_slice, per_sprint, per_project}`. Slice-11's
  `/events` SSE snapshot can include this verbatim under `aggregates`.
- **Browser store contract**: `metrics.ts` is store-shape-agnostic
  (`MetricsStoreLike` duck type). Slice-11's pub/sub store satisfies it as-is
  via `getState` / `setState`. No new dependency on the store internals.

## File touches relative to the slice contract
- Spec listed `naml/server.py` in the slice's touches; **deliberately not
  modified here** because slice-11's server.py changes are not yet in this
  worktree. The wiring lives in `naml/metric_tick.attach(...)` ready for a
  one-line call from the merged server.py. Net effect on the orchestrator's
  tier-1 merge: no conflict on `server.py`.
