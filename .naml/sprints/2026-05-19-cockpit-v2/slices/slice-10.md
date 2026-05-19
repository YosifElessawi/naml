# slice-10: In-memory aggregator + file watcher + cold-start replay

## What to build

The Python server's metrics engine. Watches every `state/<slice>.tokens.jsonl`
file across all sprints for the active project, maintains in-memory aggregates,
and rebuilds those aggregates from scratch on cold start.

### New modules

1. **`naml/aggregator.py`** — pure-data class holding:
   - `per_slice: dict[slice_id, SliceMetrics]` — running totals per slice
   - `per_sprint: dict[sprint_id, SprintMetrics]` — sprint rollups (Σ slices)
   - `per_project: ProjectMetrics` — today / week / 30d / lifetime $ + tokens
   - Methods: `apply_event(jsonl_dict)`, `snapshot()`, `reset()`
   - All operations O(1) per event.
2. **`naml/watcher.py`** — `watchdog`-based file watcher:
   - Watches `.naml/sprints/*/state/*.tokens.jsonl`
   - On file modification: read new lines since last position
     (track per-file position in memory), feed them to the aggregator
   - Debounces rapid writes (50ms coalesce)
3. **Cold-start replay** in `naml/server.py`:
   - On server boot, scan all `.naml/sprints/*/state/*.tokens.jsonl`
   - Read each file fully, feed to aggregator
   - Record final byte-position per file so the watcher only reads new content
   - Log `cold start replay: N events in Tms`
4. **HTTP endpoints** (used by Settings → Advanced → Reset Aggregates):
   - `POST /aggregates/reset` — recompute from disk
   - `GET /aggregates` — return current snapshot (used by slice-12 SSE)

### Dependencies

- Add `watchdog>=3.0` to `pyproject.toml` runtime deps. First non-stdlib runtime
  dep for naml — this is intentional (the user is OK with it, see spec).

### Tests (`tests/test_aggregator.py`, `tests/test_watcher.py`)

- Aggregator: apply 100 events, snapshot matches expected totals
- Aggregator: today / week / 30d / lifetime boundaries computed correctly
  across DST transitions (use UTC throughout)
- Watcher: writing a new JSONL line triggers `apply_event` within 200ms
- Cold-start: simulate a 6-month-old project's JSONL files (~200 files),
  replay completes in < 2s

## Acceptance criteria

- [ ] `Aggregator.apply_event` is O(1) per event
- [ ] Watcher fires within 200ms of a JSONL append
- [ ] Cold-start replay rebuilds all aggregates correctly
- [ ] `GET /aggregates` returns the snapshot shape documented in spec.md
- [ ] `POST /aggregates/reset` recomputes correctly
- [ ] `watchdog` dep added to `pyproject.toml`
- [ ] All tests pass (Python tests, no UI changes yet)

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q8b-metrics-sync.html

## Constraints

- **In RAM only.** No DB, no SQLite, no Redis. Aggregates die with the server
  process and are rebuilt on next boot from JSONL.
- **Per-file byte position** must survive a server restart (write it to a
  `.naml/state-cursor.json` after each watcher batch). Otherwise cold-start
  reads everything every time.
- **Time-window boundaries** use UTC throughout. Convert to local time only
  at display time (browser-side).
- **Project attribution**: derived from sprint path (`.naml/sprints/<id>/`
  → project = current working directory). Cross-project rollups only work
  if a parent process owns multiple project paths (cross-project view is
  out of scope for this sprint).

## Notes from grilling session

This is the central piece that makes "no DB" feasible. Test cold-start
performance early — if 2s is hard, the architecture is wrong somewhere.

`watchdog` on macOS uses FSEvents under the hood and is fast. On Linux you
get inotify with its per-process watch limit; document the workaround
(`fs.inotify.max_user_watches`) in `web/README.md`.
