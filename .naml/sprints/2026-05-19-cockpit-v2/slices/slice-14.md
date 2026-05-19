# slice-14: Held slice state + HOLD intervention + drawer gating polish

## What to build

The last slice — the safe-intervention story. Adds the `held` slice state
to naml's state machine and wires the drawer's HOLD button + Open-in-terminal
button to real server-side behavior.

### Python — state machine

1. **Add `held` to the state set** in `naml/states.py`:
   ```python
   HELD = "held"
   ```
   Update the state-machine transition table (`naml/states.py`):
   - `setup → held` (user pressed HOLD before any work started)
   - `work → held` (user pressed HOLD mid-conversation)
   - `held → work` (user pressed RESUME — re-engages the lane)
   - `held → failed` (user pressed MARK FAILED while held)
   - `held → abandoned` (user pressed SKIP while held)
2. **`pr` state is NOT held-able.** Once the PR is open, the implementer
   session is already at rest; user can open terminal directly.
3. **Lane worker honors HOLD requests**:
   - Lane reads a `state/<slice>.hold_requested` flag file before starting
     each turn. If present, the lane:
     a. Waits for the current turn to settle
     b. Records transition to `held`
     c. Releases the worktree lock (so user can `cd` and `git` in it)
     d. Spins, watching the same flag file for removal (RESUME)
   - On RESUME: lane re-enters `work` state, resumes the Claude session
     (`claude --resume <session-id>`), continues.
4. **Hold flag file**: `state/<slice>.hold_requested` (zero-byte sentinel).

### Server — intervention endpoints

In `naml/server.py`:

- `POST /intervene/<slice-id>?action=hold` — writes the sentinel; returns 200
- `POST /intervene/<slice-id>?action=resume` — deletes the sentinel; returns 200
- `POST /intervene/<slice-id>?action=open-terminal` — shells out to
  `open -a Terminal -n` with a script that runs `cd <worktree> && claude --resume <session-id>`. Refuse (HTTP 409) if slice state is not in the
  unlocked set (`held · review · merged · failed · needs_human_review · abandoned · blocked_upstream`).
- `POST /intervene/<slice-id>?action=mark-failed` — transitions to `failed`
- `POST /intervene/<slice-id>?action=skip` — transitions to `abandoned`

### Browser — drawer wiring

The drawer (built in slice-7 with stubbed handlers) gets real wiring:

1. **Open-in-terminal button** — sends POST; on 200 shows "Opening terminal…"
   flash; on 409 (locked) shows the error inline ("Slice is in WORK state").
2. **HOLD button** — sends POST; UI flips Open-in-terminal to enabled when
   the slice state transitions to `held` (via the existing SSE state-update
   stream). Show a "Hold requested · waiting for current turn" flash; switch
   to "Held · terminal unlocked" once `state-update` confirms.
3. **RESUME button** (new — appears only when state is `held`) — sends POST,
   slice transitions back to `work`.

### Tests

- Python: `tests/test_held.py`
  - HOLD → `held` transition happens after current turn completes
  - RESUME → `work` transition, lane resumes the right session
  - Lane crash during HELD doesn't lose the held state
- Browser: drawer test
  - Drawer in WORK state shows Open-in-terminal locked
  - HOLD button transitions drawer to show RESUME + unlocked terminal

## Acceptance criteria

- [ ] `held` is a real slice state (in `naml/states.py`, in `SliceStatus`,
      in the state diagram in `docs/DESIGN-V2.md`)
- [ ] Pressing HOLD in the drawer pauses the lane after current turn
- [ ] Open-in-terminal is unlocked when slice is in `held` (and the other
      resting states)
- [ ] RESUME from `held` re-engages the lane, slice goes back to `work`
- [ ] All existing tests still pass (no regression in lane / state machine)
- [ ] `tests/test_held.py` passes
- [ ] Drawer wiring tests pass

## Artifacts

- ../artifacts/spec.md (Q10 gating logic section)
- ../artifacts/mockups/q10-slice-detail.html

## Constraints

- **Hold must wait for the current turn to settle.** Don't kill the Claude
  process mid-stream — that loses output and the session can't cleanly resume.
- **Open-in-terminal only via Terminal.app on macOS** for now. On other
  platforms, refuse with a helpful error. Cross-platform support is a
  follow-up sprint.
- **Sentinel file approach** is intentional — it survives a lane process
  restart, doesn't need IPC, easy to debug (you can `touch` and `rm` it
  manually).
- **Update `docs/DESIGN-V2.md`** — the per-slice state machine diagram needs
  `held` added.

## Notes from grilling session

This slice closes the loop on the "Open in terminal is locked while naml owns
the session" decision. Without it, the drawer's gating is theater — the
button is locked but there's no safe path.

HOLD is the most user-facing intervention naml has. It's also the riskiest
to implement wrong: a HOLD that doesn't wait for the current turn, or a
RESUME that resumes the wrong session, corrupts the run worse than the
problem it's solving. Write the tests first.
