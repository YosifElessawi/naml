# Sprint: naml V2 cockpit — web UI + live telemetry pipeline

## Goal

Build the V2 cockpit web UI (Phase 6 in the roadmap) that lets a developer
watch, intervene in, and reason about an in-flight naml sprint at a glance.

Concretely:

- A single-page web app served at `localhost:<port>` by the naml CLI.
- Three persistent zones: header, route-driven main pane (Dashboard / Sprint
  / Settings), always-on right rail (sync · activity · cost timeline · quotas).
- A live data pipeline that pushes state transitions and metric ticks from
  the running orchestrator to the browser with under ~120ms total latency.
- No database. JSONL on disk is the source of truth; in-memory aggregates
  drive the UI; SSE is the wire.

## Why now

Naml hit MVP today (PR #21 — tiered merger). The merge pipeline works
end-to-end. The blocker on dogfooding naml on its own codebase is **visibility**:
without the cockpit, an operator running `naml run` watches log lines and
hopes things are happening. Phase 6 is what turns naml from a CLI into a
tool you can trust unattended.

This sprint is the first big self-host stress test. The whole work was
pre-grilled (Q1–Q10 captured in `artifacts/spec.md`) and pre-mocked
(`artifacts/mockups/`) before naml could run it, so the slice work is
*implementation*, not *design*.

## Non-obvious constraints

- **Self-host run: do not restart the orchestrator mid-sprint.** This
  sprint modifies several files that naml's own runtime imports
  (`naml/lane.py`, `naml/state.py`, `naml/states.py`, `naml/cli.py`,
  `naml/server.py`, removal of `naml/web.py`). Slice changes live in
  per-slice worktrees until merge; once merged, the running orchestrator
  process keeps using the modules it loaded at startup. If you Ctrl-C
  and re-run `naml run` mid-sprint, the next round will pick up
  partially-merged self-modifications and could break the orchestrator
  on the spot. If a hard restart is unavoidable, abandon the sprint and
  open the remaining slices manually.
- **Lanes own Claude sessions.** "Open in terminal" from the slice drawer
  would interrupt an in-flight session. The button is **gated** by slice
  state: locked in `setup · work · pr`; unlocked in
  `held · review · merged · failed · needs_human_review · abandoned`.
  A new `held` state is introduced to give the user a safe-intervention
  path (HOLD pauses the lane, transitions to `held`, unlocks the terminal).
- **No DB.** JSONL append-only per slice for token events; in-memory
  aggregator in the server process. Cold-start rebuilds aggregates in < 2s
  for a 6-month-old project. Adding SQLite is explicitly out of scope.
- **Live cost telemetry requires `claude -p --output-format stream-json`.**
  The lane must spawn Claude this way and parse the `usage` block per turn.
  Without it, cost data only arrives at session end.
- **Context-window % is the most actionable signal.** Amber at 70%, red
  at 85%. Drives the smoke alarm on slice cards.
- **Cost timeline (today / week / 30d / lifetime) is the headline.** Every
  surface answers "how much have I spent" before anything else.
- **3 lanes by default.** Bumped from V2-doc default once the cockpit is
  stable enough to handle visual saturation past 3 columns.
- **Smoothness matters.** State transitions animate over ~400ms with FLIP
  reorder, counter tweens, and pulse re-starts. Sync-dot has 4 states
  (LIVE / SLOW / LOST / CONNECTING) driven by SSE heartbeat (2s) with
  5s / 15s thresholds.
- **Tier-1 hit % gets prime real estate in the hero.** It's the single
  best merge-pipeline health signal — when it drops, something is wrong
  with the auto-review or the gates.

## Out of scope

- **Menu-bar app.** Web-first only. Menu-bar is roadmap Phase E, post-OSS.
- **Cross-project analytics view.** Cockpit is per-project. If a cross-project
  "all projects today $" view is wanted later, that's a follow-up sprint;
  may justify SQLite then.
- **JSONL compaction.** Defer to v2 of this work — files stay raw until
  they become a problem (won't for years on a personal tool).
- **Mobile / tablet layouts.** Desktop-only. The right rail steals ~280px
  permanently; that's fine on a laptop, unworkable on a phone.
- **Authentication.** Local-only server bound to `127.0.0.1`. No login.
- **Multi-user collaboration.** Naml is a single-developer tool.
- **Editing slice spec mid-run from the UI.** The slice spec is the
  package on disk; mutations happen via `/grill-feedback` → child sprint.
- **Replaying / re-running a completed sprint from the UI.** Re-run is a
  CLI flag (`naml run --resume`); no UI button for it yet.
- **In-cockpit grilling.** `/grill-me` stays in the terminal as a Claude Code
  skill. Cockpit displays the result; it does not host the conversation.

## Reference artifacts

Every slice prompt should load these alongside `overview.md`:

- `artifacts/spec.md` — full design captured from the Q1–Q10 grilling session
- `artifacts/mockups/q1-shell.html` through `q10-slice-detail.html` — visual
  references for each surface
- `docs/DESIGN-V2.md` — V2 architecture doc (sprint/slice/lane/state-machine
  vocabulary)

The mockups are pixel-faithful enough that they double as the design spec.
Where a slice prompt says "match the Q3 dashboard mockup", implementers
should open `artifacts/mockups/q3-dashboard.html` and replicate the recommended
(badged) option's layout and styling exactly.
