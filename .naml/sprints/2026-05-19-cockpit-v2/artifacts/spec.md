# naml V2 cockpit — design spec

> Captured from a `/grill-me` session on 2026-05-19.
> Every decision below is grounded in a visual mockup at `artifacts/mockups/`.
> Implementers should treat the mockups as the source of truth for layout +
> styling; this doc captures the *why* + the data contracts.

---

## Q1 — Shell layout

**Decision:** Header + Main + always-on Right Rail.

Three persistent zones. The right rail (~280px) carries sync dot, activity
ticker, cost timeline (today/week/30d/lifetime), session + weekly quotas.
It collapses to an 18px icon-strip via `⌘\` when the user needs width.

**Why:** Telemetry must live in peripheral vision. Quotas dying mid-run
should never be a surprise. The right rail is what makes this feel like
a cockpit, not a web app.

**Visual ref:** `mockups/q1-shell.html` Option A.

---

## Q2 — Header

**Decision:** Slim single-row.

Contents, left-to-right:

1. `▟ naml` logo
2. Sync dot + "Synced 2s" badge
3. Project switcher (`inpolicy/web ▾`)
4. Active sprint pill (with live mini progress bar, slices `5/8`)
5. Spacer
6. Tabs: Dashboard · Sprint · Settings
7. 🔔 notifications bell (with red dot on escalations)
8. Account chip (`personal` ▾)
9. ⚙ gear

**Run Sprint / Merge Sprint stay on the Sprint view** — not in the header.
Context-sensitive actions don't belong in always-on chrome.

**Sprint pill behaviour:** left-click → jumps to Sprint view; right-click →
context menu (pause / pin different sprint).

**Notification bell scope:** both escalations (`needs_human_review`,
`failed`, `merge_blocked`) and ship-ready celebrations
(`awaiting_signoff`).

**Visual ref:** `mockups/q2-header.html` Option A.

---

## Q3 — Dashboard

**Decision:** Hero card + sprint timeline + feedback inbox sidecar.

**Hero card** (full width, top of Dashboard):
- Project name + state pill (`ACTIVE` with pulsing dot)
- Sub-line: lane count · branch · sprints shipped · "last merge Nm ago"
- 4-stat row, right-aligned: today $ · tokens today · tier-1 hit % · avg sprint duration

**Sprint timeline** (left ~2/3 of main pane below hero), grouped:
- **ACTIVE** (1) — full-width sprint row with mini state-strip (one cell per
  slice colored by its state)
- **QUEUED** (n) — smaller rows for planned sprints
- **RECENT** (last 3) — for context + tier-1-hit pride

Sprint row contents: state-strip · title · state pill · meta (slices · cost) ·
OPEN button.

**Feedback inbox sidecar** (right ~1/3 of main pane below hero):
- Count of unfiled bullets (warning amber if > 3)
- 3 most-recent bullets with source line
- `/grill-feedback →` CTA

**Visual ref:** `mockups/q3-dashboard.html` Option A.

---

## Q4 — Sprint state machine viz

**Decision:** Horizontal pill stepper + slice sub-rail inside the `executing` pill.

6 pills on a rail: Planning → Publishing → **Executing** → Awaiting sign-off
→ Merging → Complete. Current pill pulses, completed are solid green, future
are dim. Duration under each pill (`12s`, `4s`, `21m · ETA 14m`, etc.).

Inside the `executing` pill: a 5px-tall sub-rail of slice-state cells (one
per slice, colored by state) so you can read the slice shape *without leaving
the sprint state view*.

Below the stepper: **aux strip** with `elapsed · ETA · cost · proj total · lanes · slices · retries · tier-1 so far · LGTM so far`.

Below aux: **alternate exits** strip — muted display of failure paths
(`partial_failure`, `failed`, `merge_blocked`). Light up only when hit.

**ETA estimator:** `mean per-slice duration so far × remaining slice count`.
Simple linear extrapolation. Switch to slice-type p50 once enough history.

**Visual ref:** `mockups/q4-sprint-state.html` Option A.

---

## Q5 — Slice cards

**Decision:** Dense telemetry card (~110px).

Three rows + traversal chain:

**Row 1:** slice ID chip · title · AFK/HITL badge · lane chip · state pill (pulsing dot).

**Row 2:** dual progress bars side-by-side
- **DURATION · IN STATE** with `6m12s · ~9m budget` and a budget tick mark
- **CONTEXT WINDOW** with `73% · 146k / 200k` — **amber 70%, red 85%**

**Row 3 (stats strip):** cost · tokens (in/out/cache) · retry n/cap (hidden when 0) · last gate · session id with action buttons (`⎘` copy, `▶` open terminal, `⎇` view branch).

**Traversal chain** underneath: `setup → work (retry 1/2) → pr → review → merged`
with done/current pill styling.

**State variants shown:** `work` · `review` · `merged` · `blocked` · `failed`.

**State stripe** on left edge of card colored by state (3px).

**Visual ref:** `mockups/q5-slice-card.html` Option A.

---

## Q6 — Lane layout

**Decision:** Hybrid 2 — split gantt (top) + kanban current state (below) + queue strip (bottom).

**Top half:** compact horizontal gantt with 3 swimlanes. Time on x-axis.
Slice blocks sized by duration. "Now" cyan vertical bar. Ghost blocks past
"now" preview the scheduler plan. Idle lanes show a glowing dashed `⌁ READY`
band where their track would be.

**Bottom half:** 3 kanban-style cards showing only the **current** state of
each lane (lane name + state pill + current slice ID + title + live dur/ctx
meters). Idle lane card shows `⌁ READY · NEXT SLICE WILL LAND HERE`.

**Queue strip** at the bottom: lists ready / blocked slices. Each chip shows
which lane it's likely to land on next.

**Default lane count:** 3.

**Visual ref:** `mockups/q6b-lane-hybrids.html` Hybrid 2.

---

## Q7 — Metrics

**Decision:** Comprehensive hybrid. Cost timeline (today / week / 30d /
lifetime) is the headline. All other metrics preserved, layered across surfaces.

### Right rail (always-on)
- Sync header with "Synced Ns ago"
- Activity ticker (3-4 most-recent state transitions)
- **Cost Timeline block** — boxed: today / this week / last 30d / **lifetime**
  + 14-day daily-cost sparkline
- Session quota bar + "resets in Nm"
- Weekly quota bar + "resets in Nd · est empty Thu 6pm"
- This-project share of weekly + lifetime (per-project attribution rollups)

### Dashboard hero — primary cost cluster
4 big numbers, equal weight: **Today** · **This Week** · **Last 30 Days** ·
**Lifetime · project**. Each has a sub-line (tokens, % delta vs prior period).

### Dashboard hero — secondary health row (6 stats with 7d trend)
1. Tier-1 hit %
2. Avg sprint duration
3. Sprints / week
4. Slice fail rate (border-left amber when > 5%)
5. LGTM first-pass %
6. Mean review iterations

### Dashboard hero — tertiary row (6 stats, can collapse)
$/sprint avg · $/merged PR · TTFP median · retry rate · peak ctx avg · DAG util.

### Sprint aux strip (Sprint view)
elapsed · ETA · cost so far · proj total · lanes 3/3 · slices 5/8 · retries ·
tier-1 so far · LGTM so far · last-tick.

### Slice stats row (each slice card)
dur · ctx % · cost · proj total · tokens (in/out/cache) · retry n/cap · gate ·
last error · session id.

### Settings → Health page
Trend graphs (cost 30d, tier-1 30d, avg sprint 30d, slice fail 7d, LGTM 1st pass,
per-skill cost breakdown) + per-state retry breakdown + reviewer/implementer
disagreement rate + TTFP histogram.

**Visual ref:** `mockups/q7b-metrics-hybrid.html`.

---

## Q8 — Sync transport (state)

**Decision:** Server-Sent Events (SSE) driven by a file watcher.

- Python `watchdog` watches `.naml/sprints/*/state/`.
- On any atomic JSON rewrite, the server emits a `state-update` SSE event
  with the delta.
- Browser `EventSource` patches in-memory store + animates the affected card.
- **Heartbeat:** 2s. Drives the "Synced Ns ago" badge.
- **Sync-dot thresholds:** LIVE while < 5s since last event; SLOW at 5–15s;
  LOST after 15s.
- **Initial state on connect:** server sends a full snapshot (every sprint +
  slice) as the first event; subsequent events are deltas.
- **Reconnect:** native EventSource backoff + `Last-Event-ID` catch-up.

### Animation spec (Q8 smoothness table)
| Element | Animation |
|---|---|
| State pill color | 400ms tween |
| State pill text | cross-fade 200ms |
| Card border stripe | 400ms tween |
| Pulse dot | stop → restart on new state |
| Card re-order | FLIP 300ms ease-out |
| Activity ticker entry | slide-in 250ms from top |
| Numeric counter | tween 600ms |
| Progress bar width | 400ms ease |
| Sync dot color | 200ms tween + pulse cadence varies |
| Slow/lost timestamps | desaturate 600ms |

**Visual ref:** `mockups/q8-sync.html` Option A.

---

## Q8b — Metrics architecture

**Decision:** No DB. Append-only JSONL per slice + in-memory aggregator in the server.

### Pipeline
```
Claude --output-format stream-json
  → lane parses usage block per turn
  → append one line to state/<slice>.tokens.jsonl
  → fsevents fires
  → server reads new lines, updates aggregator dict
  → SSE metric-tick event
  → browser EventSource patches store
  → DOM tweens counters / bars
```
Total latency: **~120ms** (28 + 15 + 50 + 22 + 25 across stages).

### JSONL line schema (`state/<slice>.tokens.jsonl`)
```json
{ "t": "2026-05-19T14:23:14.812Z", "slice": "slice-4", "session": "9b0c…ef13",
  "turn": 12, "tokens_in": 2143, "tokens_out": 488, "cache_read": 98000,
  "cache_write": 0, "cost_usd": 0.0124, "ctx_pct": 73 }
```

### Aggregator (Python dict, in RAM)
- `per_slice[<id>]` — live cost, tokens, ctx %, last_event_at
- `per_sprint[<id>]` — Σ slice costs + tokens + tier-1-so-far + LGTM-so-far
- `per_project[<id>]` — `today`, `this_week`, `last_30d`, `lifetime` totals
- ~3MB RAM for a 6-month-old project · O(1) update per token event

### Cold-start replay
On server start, scan all `.naml/sprints/*/state/*.tokens.jsonl` for the project,
build aggregates in memory. Bounded by I/O; < 2s for ~50MB total.

### SSE event types
- `event: state-update` — slice / sprint state transitions
- `event: metric-tick` — token deltas + fresh rollups

### Context window %
Computed as `(tokens_in + cache_read) / model_context_max`. Model + context-max
sourced from `.naml/config.toml`, not hardcoded.

### Lifetime origin
First JSONL event observed for this project. Set automatically.

### Cross-project today $
Server scans all per-project JSONL files on cold start; caches account-level
aggregate in RAM. No central global usage file — avoids double-write.

**Visual ref:** `mockups/q8b-metrics-sync.html`.

---

## Q9 — Settings layout

**Decision:** Tabbed left-nav.

Tabs, top-to-bottom: **Project · Lanes · Gates · Account · Sync · Health · Advanced**.
"+ Add project…" anchored at bottom of nav (runs `/setup-naml`).

### Project tab
- Repo slug (verified badge)
- Base branch (auto-detected)
- Project root path
- ADR folder path
- Lifetime tracking since (auto, read-only)
- Labels: sprint/slice prefixes + lifecycle label list (chip-style with `×`)

### Lanes tab
- Default parallel lanes (1–8 slider, default 3)
- DAG-width detection toggle (on)
- Hard cap

### Gates tab
- Ordered list of named gates (lint, typecheck, test, build)
- Each row: name + argv input + last-run status (✓ / ▶ running / ✗)
- Cheapest-first ordering enforced

### Account tab
- Active `CLAUDE_CONFIG_DIR` (dropdown)
- Model (dropdown)
- Context window (auto-pulled, read-only)
- Plan caps (session / weekly token limits) + reset anchors

### Sync tab
- SSE heartbeat (default 2s)
- Sync thresholds (5s / 15s)
- Reduce-motion toggle (default off)

### Health tab
- 6 trend cards (daily cost 30d · tier-1 30d · avg sprint 30d · slice fail 7d ·
  LGTM 1st · per-skill cost)
- Per-state retry breakdown
- Reviewer/implementer disagreement rate
- TTFP histogram
- Link out to per-sprint summary archive

### Advanced tab
- Feature flags
- JSONL compaction trigger
- Reset aggregates (rebuild from disk)
- Wipe sprint history (destructive, double-confirm)
- Raw `config.toml` editor

### Save behaviour
Autosave on field blur + "Saved 2s ago" badge. No modal Save buttons.

**Visual ref:** `mockups/q9-settings.html` Option A.

---

## Q10 — Slice detail drawer

**Decision:** Right-side drawer · 55% width · dim+blur underneath · cyan left-edge accent.

The cockpit (sprint state stepper + lanes + queue + aux strip) stays visible
behind the drawer, dimmed and blurred, with the clicked slice card glowing
cyan to mark it as the drawer's source.

### Drawer header
- Slice ID chip
- Title (large)
- State pill · AFK/HITL chip · lane chip
- Sub-meta: `started 14:17:43 · 6m12s · sprint 2026-05-19-html-renderer`
- Close button (top right)

### Drawer action bar
- **▶ OPEN IN TERMINAL** — gated, see below
- ⎘ copy session-id
- ⎇ checkout branch
- ↗ view PR (when ready)
- Spacer
- **⏸ HOLD & UNLOCK TERMINAL** (warn-amber)
- **✕ MARK FAILED** (danger-red)
- ⏭ SKIP

### Open-in-terminal gating
Claude sessions are **single-owner**. Resuming a session via `claude --resume`
from a separate terminal while a naml lane is mid-conversation corrupts the
run. Therefore the button is locked unless the slice is in a resting state.

| Slice state | Terminal button |
|---|---|
| `setup`, `work`, `pr` | **LOCKED** — tooltip explains why |
| `held`, `review`, `merged`, `failed`, `needs_human_review`, `abandoned`, `blocked_upstream` | **UNLOCKED** |

The **HOLD & UNLOCK TERMINAL** button is the safe path mid-run:
1. Signal the lane to stop starting new turns.
2. Wait for the in-flight turn to settle.
3. Transition the slice to `held`.
4. Now Open-in-terminal unlocks.

**RESUME** transitions `held` back to `work` (re-engaging the lane).

### `held` slice state — required new state
- Entered from any of `setup` / `work` (NOT from `pr` / `review`, which already
  imply naml is idle on the implementer session)
- Exits to `work` (RESUME) or to `failed` / `abandoned` (terminal)
- Recorded in `state/slice-<id>.status.json` like every other transition

### Drawer body sections
1. **Overview** — 2-column grid: session id (with copy), branch, worktree path,
   depends_on, touches, PR url, cost + tokens, ctx %, retry n/cap, duration.
2. **Acceptance criteria** — checklist from slice prompt. ✓ done · ▶ in progress · □ pending.
3. **State machine traversal** — vertical timeline of transitions with timestamps,
   durations, and detail. Retry events shown in amber with a `GATE FAIL` badge.
4. **Latest gate output** — monospace pre-block with the actual stderr from
   the most recent failed gate.
5. **Auto-review** — verdict box (LGTM / REQUEST_CHANGES) with body text once
   the slice reaches `review`. Placeholder otherwise.
6. **Notes from grilling session** — verbatim content of `slices/slice-N.md`'s
   "Notes from grilling session" section.

### Close affordances
× button · click outside · `esc` key.

**Visual ref:** `mockups/q10-slice-detail.html` Option A.

---

## Summary cheat sheet for implementers

```
SURFACES (4 + 1 overlay):
  Header               Q2 — slim, always-on
  Main pane            routes between Dashboard / Sprint / Settings
  Right rail           Q1 — telemetry, always-on
  Slice drawer         Q10 — overlay, 55% width, gated terminal button

ROUTES:
  Dashboard            Q3 — hero + timeline + inbox
  Sprint               Q4 stepper + Q6b lane hybrid + Q5 slice cards
  Settings             Q9 — tabbed, includes Health

DATA PIPELINE:
  Claude stream-json   → lane parses usage
  state/<slice>.tokens.jsonl    ← source of truth
  Python aggregator (RAM, ~3MB)  ← derived rollups
  SSE                  → 2 event types: state-update + metric-tick
  EventSource → browser store → DOM tween

NEW SLICE STATE:
  held                 — paused-by-user, unlocks intervention

CADENCES:
  SSE heartbeat        2s
  Sync SLOW threshold  5s
  Sync LOST threshold  15s
  State transition     ~400ms animated
  Latency disk→DOM     ~120ms

HEADLINE METRIC:
  Cost timeline (today / week / 30d / lifetime) on every cockpit surface.
  Tier-1 hit % on the hero — best single health signal.
```
