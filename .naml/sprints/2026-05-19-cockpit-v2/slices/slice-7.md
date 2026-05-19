# slice-7: Slice detail drawer + Open-in-terminal gating

## What to build

The slice detail drawer (Q10 Option A). Opens from any slice card's
"OPEN ↗" button. Slides in from the right at 55% width over a dimmed +
blurred cockpit underneath.

Component at `web/src/components/SliceDrawer/`. Sections (top → bottom):

1. **Header** — slice ID chip · title · state pill · AFK/HITL · lane chip · sub-meta (started · duration · sprint id) · close button
2. **Action bar** — buttons:
   - **▶ OPEN IN TERMINAL** — *gated, see below*
   - `⎘` copy session-id
   - `⎇` checkout branch
   - `↗` view PR (when ready; disabled until state ≥ `pr`)
   - Spacer
   - **⏸ HOLD & UNLOCK TERMINAL** (warn-amber)
   - **✕ MARK FAILED** (danger-red)
   - **⏭ SKIP** (ghost)
3. **Gating note** below action bar (amber callout) — explains the lock when
   the terminal button is locked
4. **Overview section** — 2-column grid: session id (w/ copy), branch, worktree,
   depends_on, touches globs, PR url, cost+tokens, ctx %, retry, duration
5. **Acceptance criteria** — checklist (✓ done · ▶ in progress · □ pending)
6. **State machine traversal** — vertical timeline of transitions w/ timestamps,
   durations, detail lines. Retry events shown in amber w/ `GATE FAIL` badge
7. **Latest gate output** — monospace pre-block (real stderr if a gate failed)
8. **Auto-review verdict** — verdict box (LGTM / REQUEST_CHANGES) w/ body
   text once state ≥ `review`. Placeholder otherwise
9. **Notes from grilling session** — verbatim from the slice's prompt file

### Open-in-terminal gating

The button is **locked** unless slice state is in:
- `held`, `review`, `merged`, `failed`, `needs_human_review`, `abandoned`, `blocked_upstream`

Locked states (`setup`, `work`, `pr`) show a tooltip:
*"Slice is in WORK · resuming the session in a terminal would interrupt naml's run. HOLD first."*

The "HOLD & UNLOCK TERMINAL" button is the user's safe path. Clicking it
should POST to `/intervene/<slice-id>?action=hold` — the actual `held`
state implementation lands in slice-14; for this slice, just wire the button
to a POST and a status flash. The Python side becomes real in slice-14.

### Close affordances

× button · click outside · `esc` key. All three close the drawer.

## Acceptance criteria

- [ ] Drawer matches `mockups/q10-slice-detail.html` Option A
- [ ] Slides in from the right at 55% width with smooth (300ms) translateX
- [ ] Dim+blur applied to the cockpit underneath (`backdrop-filter: blur(2px)`)
- [ ] Open-in-terminal button is visually locked (greyed + padlock) when
      slice is in `setup`/`work`/`pr` state
- [ ] Tooltip on the locked button explains why
- [ ] HOLD button sends POST to `/intervene/<id>?action=hold` and shows a
      "Hold requested" flash (real handler in slice-14)
- [ ] Drawer scrolls independently of the cockpit underneath
- [ ] `esc` / click-outside / × all close the drawer
- [ ] All `web-*` gates pass

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q10-slice-detail.html

## Constraints

- **Drawer width 55%**. On viewports < 1024px wide, the drawer goes full-width.
- **Open-in-terminal command**: clicking opens a new Terminal.app window
  (via `open -a Terminal -n` shell-out) running `claude --resume <session-id>`
  at the worktree path. Server-side endpoint handles the shell-out (POST
  `/intervene/<id>?action=open-terminal`).
- **Action POST endpoints** all live in the Python server. This slice adds
  the POST handlers; slice-14 implements the `held` state machine logic
  that the HOLD handler triggers.
- **Drawer state is in the URL** (`?drawer=<slice-id>`) so a refresh keeps
  it open.

## Notes from grilling session

The gating is the most important nuance in the whole cockpit. Without it,
a developer who casually clicks "Open in terminal" mid-run corrupts their
own sprint. The HOLD-first dance must feel intentional, not painful — the
amber "HOLD & UNLOCK TERMINAL" button label is doing real work to telegraph
that this is the safe path.

Don't forget the case where the slice is *already* in a resting state when
the drawer opens — Open-in-terminal is enabled immediately; HOLD is hidden
or disabled (nothing to hold).
