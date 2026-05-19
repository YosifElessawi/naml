# slice-9: Token JSONL emitter — lane spawns Claude with stream-json, parses usage

## What to build

The first half of the metrics pipeline. The lane worker currently spawns
`claude -p <prompt>` and gets only the final result. This slice changes it
to `claude -p --output-format stream-json <prompt>`, parses each turn's
`usage` block, and appends one line per turn to a new per-slice JSONL file.

### Concretely

1. **Modify `naml/claude.py`** (or wherever lanes invoke Claude). Add
   `--output-format stream-json` to the argv. Read the process's stdout line
   by line — each line is a JSON object.
2. **Parse usage blocks.** Claude emits multiple object types; the ones we
   care about have shape `{"type": "assistant", "usage": {...}}`. Extract:
   - `input_tokens` (current turn's input)
   - `output_tokens` (current turn's output)
   - `cache_read_input_tokens`
   - `cache_creation_input_tokens`
   - `total_cost_usd` (Claude provides this per turn)
3. **Compute context window %.** `(input_tokens + cache_read) / model_context_max`.
   `model_context_max` comes from `.naml/config.toml` (`[claude]` table).
4. **Append a JSONL line** to `state/slice-<id>.tokens.jsonl` for each turn.
   Schema (must match `artifacts/spec.md` exactly):
   ```json
   {"t": "<iso8601>", "slice": "slice-4", "session": "<short-id>",
    "turn": 12, "tokens_in": 2143, "tokens_out": 488, "cache_read": 98000,
    "cache_write": 0, "cost_usd": 0.0124, "ctx_pct": 73}
   ```
5. **Atomic append**: open in `O_APPEND` mode, write one full line + `\n`,
   close. Or use `pathlib.Path.write_text` with append flag if more readable.
6. **State path helpers.** Add `tokens_path(sprint_root, slice_id) -> Path`
   in `naml/state.py` next to the existing `summary_path` / `status_path`
   helpers.

### Tests (`tests/test_tokens.py`)

- Parsing a captured `stream-json` fixture produces the right JSONL lines
- Appending is atomic (concurrent writers don't interleave)
- A malformed JSON line in stdout is logged + skipped, doesn't crash the lane
- Context % calculation handles missing `cache_read` gracefully

## Acceptance criteria

- [ ] Lanes spawn Claude with `--output-format stream-json`
- [ ] Per-turn `usage` blocks parsed without losing any field
- [ ] One line appended to `state/slice-<id>.tokens.jsonl` per turn
- [ ] Line schema matches `artifacts/spec.md` exactly
- [ ] Existing per-slice retry / completion-detection logic still works
      (stream-json doesn't break the completion signal)
- [ ] `tests/test_tokens.py` passes
- [ ] All existing tests still pass

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q8b-metrics-sync.html

## Constraints

- **No new runtime deps.** stdlib `json` + file I/O is all you need.
- **Don't break existing lane retry logic.** The lane currently parses
  Claude's output to detect completion / next-turn-needed; that detection
  must keep working with `stream-json` input.
- **Log malformed lines** to `stderr` for debugging but don't fail the run.
- **Session ID** in the JSONL line is the slice's session ID (already in
  `SliceStatus.session_id`), not parsed from the stream.

## Notes from grilling session

This is the backend half of "live cost telemetry". Without it, the UI's
cost timeline only updates at session end — which kills the whole "synced
with the run" feel.

Stream-json is the official path for getting per-turn data. Keep the parsing
defensive — Claude's output format evolves, and a future field shouldn't
crash the lane.
