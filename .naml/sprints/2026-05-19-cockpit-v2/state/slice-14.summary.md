# slice-14 — held slice state + HOLD/RESUME + drawer wiring

## What changed
- **State machine.** `naml/states.py` gains `HELD = "held"` plus a new
  `TERMINAL_UNLOCKED_STATES` frozenset (the Python source of truth for
  the open-in-terminal gate). `held` is intentionally NOT in
  `LANE_DONE_STATES` or `LANE_FAILED_STATES`.
- **Sentinel.** `naml/state.py` adds `hold_requested_path` /
  `is_hold_requested` / `write_hold_requested` / `clear_hold_requested`.
  The sentinel file is `state/<slice>.hold_requested` (zero-byte), durable
  across naml-run invocations.
- **Lane HOLD honouring.** `naml/lane.py` adds `_await_hold_clearance`;
  called at every turn boundary (initial enter-work, after every
  implementer call, between gate-fix retries, in the review fix loop).
  HOLD always waits for the current Claude turn to settle — we never
  kill the subprocess mid-stream. Transitions held → work are recorded
  on every park / resume so the cockpit timeline reads correctly.
- **Scheduler.** `naml/scheduler.py:absorb_existing_statuses` special-cases
  `held` so a crash during HOLD doesn't auto-recover the slice to
  `pending` (which would wipe `session_id`). Re-creates the sentinel if
  it got removed externally.
- **Server.** `naml/server.py` adds `POST /intervene/{slice_id}?action=…`
  supporting `hold | resume | mark-failed | skip | open-terminal`.
  open-terminal is gated by `TERMINAL_UNLOCKED_STATES` (409 if locked),
  is macOS-only for now (501 elsewhere), shells out to Terminal.app via
  AppleScript with `claude --resume <session-id>` in the worktree.
  Helpers `_intervene_response` and `_resolve_sprint_root_for_slice` are
  exposed at module level so the tests can drive them without an event
  loop.
- **Browser wiring.** `web/src/components/SliceDrawer/intervene.ts` is a
  new fetch wrapper exporting `intervene(sliceId, action)` plus a TS
  mirror of `TERMINAL_UNLOCKED_STATES` / `isTerminalUnlocked`. Pairs with
  slice-7's `onIntervene` drawer prop. Never throws — even network
  failures resolve as `InterveneFailure` with `status: 0` so the
  drawer's flash bar always gets actionable text.
- **Tests.** New `tests/test_held.py` (sentinel round-trip, await-hold
  transitions, state-machine membership, absorb-existing-statuses
  preservation, every intervene action including 409 for locked
  open-terminal and 501 on non-Darwin). New
  `web/src/components/SliceDrawer/intervene.test.ts` (URL shape, 409
  conflict mapping, network error mapping, slice-id encoding).
- **Docs.** `docs/DESIGN-V2.md` per-slice state diagram now shows the
  setup→held / work→held / held→{work, failed, abandoned} transitions
  and a paragraph explaining the gating story. Cheat sheet updated.

## Non-obvious decisions
- **Server does NOT flip the slice state when HOLD is POSTed.** It only
  writes the sentinel. The lane owns the held transition so we can't
  race with an in-flight turn — by the time the lane sees the sentinel,
  the current Claude subprocess has already drained.
- **Open-terminal is server-gated, not just client-gated.** A stale UI
  showing the button enabled while the slice has moved back to `work`
  would otherwise crack the contract. The 409 is the source of truth.
- **`pr` and downstream are NOT held-able.** The lane is already at
  rest there — the user can open-terminal directly without needing
  HOLD first. The state diagram in DESIGN-V2.md spells this out.
- **Cross-run held preservation is sentinel-driven.** On naml restart,
  the scheduler leaves a `held` slice's `session_id` intact; the lane
  reclaims it via the normal ready-set, and the very first turn check
  re-enters the held spin loop. RESUME within the same naml run is the
  happy path; cross-restart RESUME works because the sentinel and
  session_id both persist on disk.

## For downstream slices / future work
- Drawer integration: import `intervene` from
  `web/src/components/SliceDrawer/intervene.ts` and pass as the
  `onIntervene` prop. Drawer uses the returned `InterveneResult` for
  the flash bar (success → "Hold requested · waiting for current turn",
  failure → render `result.error` inline).
- `INTERVENE_ACTIONS` on the Python side and the `InterveneAction` TS
  union are the cross-language contract — extend both in lockstep if
  new actions are added.
- The HOLD poll interval (`lane._HOLD_POLL_INTERVAL_SECONDS`, 1s) is
  per-turn so it's invisible in CPU profiles. Tunable via the
  `poll_interval=` kwarg if a future slice wants different test
  ergonomics.
