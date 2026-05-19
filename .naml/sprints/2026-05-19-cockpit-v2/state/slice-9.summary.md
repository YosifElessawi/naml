# slice-9 — token JSONL emitter

## What changed
- New `naml/tokens.py` parses Claude stream-json events into a `TurnEmitter`
  that appends one JSONL line per assistant turn to a per-slice file. Schema
  matches `artifacts/spec.md` Q8b field-for-field (`t · slice · session · turn ·
  tokens_in · tokens_out · cache_read · cache_write · cost_usd · ctx_pct`).
- `naml/claude.py`: `_spawn` now optionally tees stdout through the emitter
  (reader thread, line-buffered). `run_implementer` accepts new optional kwargs
  `tokens_jsonl_path`, `slice_id`, `model_context_max` — when set, per-turn
  token data lands live; when omitted the legacy "stdout straight to log" path
  runs unchanged. Reviewer and merger callers updated for the new 3-tuple
  return.
- `naml/lane.py`: every implementer call (work, gate-retry, post-review-fix,
  post-review gate-retry) now passes the tokens path + `cfg.model_context_max`.
- `naml/state.py`: added `tokens_path(sprint_root, slice_id)` next to
  `summary_path` / `status_path`.
- `naml/config.py`: added `[claude] model_context_max` (default 200_000).

## Files
- `naml/tokens.py` (new)
- `naml/claude.py`, `naml/lane.py`, `naml/state.py`, `naml/config.py`
- `tests/test_tokens.py` (new, 21 tests — 194 total still pass)

## Non-obvious decisions
- **Cost per turn is computed from a small static price table** keyed by model
  id (`_PRICE_PER_MTOK`). Claude's stream-json carries cumulative
  `total_cost_usd` only on the final `type:result` event, so live per-turn
  cost has to be derived. Unknown model → `cost_usd = 0.0` (tokens + ctx %
  still flow). Update the table when Anthropic prices change.
- **`usage` can live at top level OR under `message`.** The emitter handles
  both — Claude Code wraps it under `message` today; older SDKs put it at
  top level. Future-proof.
- **Atomic appends via `O_APPEND` + single `os.write`.** Two writers to the
  same JSONL file cannot interleave a partial record (POSIX guarantee, lines
  ≤ PIPE_BUF). Verified by a concurrent-write test.
- **Completion detection still uses the log file** — the reader thread tees
  every byte to the log, so `_scan_for_success_event` keeps working.
  Verified by a regression test.

## For downstream slices
- The file watcher / SSE emitter slice can `tail -f` `state/slice-<id>.tokens.jsonl`
  using `naml.state.tokens_path(sprint_root, slice_id)`.
- Each JSONL line is a complete JSON object — no partial-line reads needed.
- `cost_usd` is per turn (USD); aggregator sums across slices/sprints for the
  cost timeline. `ctx_pct` is already clamped [0, 100].
- The session id in the JSONL is the slice's session UUID (whatever
  `SliceStatus.session_id` was set to), not parsed from the stream.
