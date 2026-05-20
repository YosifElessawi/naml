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
- **Cross-restart held resume.** `process_slice` now has a fast-forward
  branch at entry: when a previous naml run died while the slice was
  held (status.state == HELD, worktree + session_id + branch all
  populated and the worktree directory still exists), the lane skips
  setup, keeps the session_id intact, and parks in the spin loop
  immediately. On RESUME the first implementer call uses
  `--resume <session-id>` with a "you were paused, continue" prompt
  (`prompts.resume_after_hold_prompt`) so the Claude session keeps its
  context. If any of the preconditions are missing (no worktree on
  disk, session_id lost, etc.) the lane falls back to a fresh setup
  rather than failing — best-effort across crashes.
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
- **React hook.** `web/src/components/SliceDrawer/useIntervene.ts` wraps
  `intervene` with the `pending` / `lastResult` / `clear` state the
  drawer needs to render the flash bar and disable buttons mid-flight.
  Lifts the React-aware piece out of `intervene.ts` so the fetch
  wrapper stays usable from non-React contexts (store subscribers,
  tests, the eventual menu-bar surface).
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
- **Cross-run held preservation is sentinel-driven AND lane-aware.**
  On naml restart the scheduler leaves a `held` slice's `session_id`
  intact; the lane's new fast-forward branch in `process_slice`
  recognises the held status, skips a fresh `worktree_add` /
  `_fresh_session_id`, and parks in the spin loop. RESUME within the
  same naml run is the happy path (lane never died). RESUME after a
  crash uses `claude --resume <session-id>` and works as long as
  Claude's session daemon still has the UUID. The fast-forward is
  guarded by preconditions (worktree dir exists, session_id present);
  if any are missing the lane falls back to a fresh setup rather than
  refusing to run.

## For downstream slices / future work
- **App.tsx wiring is deliberately left to the merge composition step.**
  slice-7's `SliceDrawer.tsx` lives in a sibling worktree at slice-14
  implementation time, so importing it here would fail tsc. After
  slice-7 + slice-14 are composed, App.tsx (or the future sprint
  route) just needs `<SliceDrawer onIntervene={(sliceId, action) =>
  intervene(sliceId, action)} ... />` or the more ergonomic
  `useIntervene(sliceId)` hook. The drawer's own RESUME button (only
  rendered when `data.state === "held"`) is a slice-7 file edit that
  can't be cleanly authored from this worktree without a 3-way merge
  conflict — flagged for either slice-7's next iteration or a
  follow-up composition slice.
- Drawer integration: import `intervene` (or `useIntervene`) from
  `web/src/components/SliceDrawer/` and pass as the `onIntervene` prop.
  Drawer uses the returned `InterveneResult` for the flash bar
  (success → "Hold requested · waiting for current turn", failure →
  render `result.error` inline).
- `INTERVENE_ACTIONS` on the Python side and the `InterveneAction` TS
  union are the cross-language contract — extend both in lockstep if
  new actions are added.
- The HOLD poll interval (`lane._HOLD_POLL_INTERVAL_SECONDS`, 1s) is
  per-turn so it's invisible in CPU profiles. Tunable via the
  `poll_interval=` kwarg if a future slice wants different test
  ergonomics.
