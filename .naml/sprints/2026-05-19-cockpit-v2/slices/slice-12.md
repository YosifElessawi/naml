# slice-12: metric-tick events + cost timeline rollups

## What to build

The second SSE event type — `metric-tick` — emitted whenever the aggregator
(slice-10) applies a new token event. Plus the time-window rollups that
populate the cost timeline (today / week / 30d / lifetime).

### Python side

1. **Time-window rollups in the aggregator** (extends slice-10):
   - `today` — sum of cost + tokens for events with `t >= start_of_today_utc`
   - `this_week` — events with `t >= start_of_week_utc` (Monday as week start)
   - `last_30d` — events with `t >= now - 30d`
   - `lifetime` — running total since first event for this project
   - Maintained incrementally: each new event adds to all applicable windows
   - A daily tick (cron-like loop in the server) rolls events out of `today`
     when the date changes
2. **`metric-tick` SSE event** — emitted after each `apply_event`:
   ```
   event: metric-tick
   data: {
     "slice": "slice-4",
     "sprint": "<id>",
     "delta": { ...the raw jsonl event... },
     "rollups": {
       "slice_cost": 0.41, "slice_tokens_in": 82140, "slice_ctx_pct": 73,
       "sprint_cost": 2.41, "sprint_tokens": 2100000,
       "project_today": 4.21, "project_week": 24.80,
       "project_30d": 118.42, "project_lifetime": 342.16
     }
   }
   ```
3. **Daily-boundary tick**: a background task runs every 60s checking if
   the day rolled over; if so, recompute `today` from disk (or maintain a
   rolling buffer of "today's events" so the rollover is O(1)).

### Browser side

1. **`web/src/store/metrics.ts`** — extends the store from slice-11. New
   slices:
   - `slice_metrics: Record<slice_id, SliceMetrics>` — live cost/tokens/ctx
   - `sprint_metrics: Record<sprint_id, SprintMetrics>`
   - `project_metrics: ProjectMetrics` — today/week/30d/lifetime + tokens
2. **EventSource handler** for `metric-tick` events — patch the relevant
   store slices.
3. **Counter tween** utility (`web/src/lib/tween.ts`): smoothly animates a
   numeric value from old to new over 600ms. Used by every numeric in the
   cockpit so cost ticks count up visibly rather than popping.

### Tests

- Python: time-window rollups handle midnight boundary correctly
- Python: lifetime aggregate matches replay-from-scratch verification
- Browser: receiving a `metric-tick` updates all relevant store slices
- Browser: counter-tween animates over 600ms with ease-out

## Acceptance criteria

- [ ] `metric-tick` SSE event emitted on every JSONL append
- [ ] All 4 time-window rollups computed correctly (UTC boundaries)
- [ ] Day rollover at midnight UTC handled without restart
- [ ] Counter tween animates costs/tokens smoothly when values change
- [ ] Browser store correctly indexes metrics by slice / sprint / project
- [ ] All gates pass

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q8b-metrics-sync.html
- ../artifacts/mockups/q7b-metrics-hybrid.html

## Constraints

- **Rollup math in Python**, not in the browser. The server is the source
  of truth; the browser is a dumb client.
- **UTC throughout** for time windows. Browser converts to local time at
  display time.
- **Counter tween** uses `requestAnimationFrame` — no setInterval.
- **Don't emit metric-tick faster than 100ms** (debounce per slice). A
  busy Claude session can emit a turn every few hundred ms; that's fine,
  but if the lane parser ever runs in a burst, the SSE channel shouldn't
  flood the browser.

## Notes from grilling session

Cost timeline is the cockpit's headline metric ("how much have I spent
total on this project?"). Get the four windows right, get the lifetime
total accurate, and the rest of the UX works.

Day rollover at midnight is a real edge case — write a test that simulates
crossing midnight while a sprint is running and verify `today` resets while
`this_week` keeps accumulating.
