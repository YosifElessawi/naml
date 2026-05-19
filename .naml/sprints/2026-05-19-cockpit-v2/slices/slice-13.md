# slice-13: Wire UI to live data + smoothness animations + 4 sync-dot states

## What to build

The big convergence slice — every UI component built in slices 2–8 (which
all use fixtures) gets wired to the live store from slices 11–12. Plus the
animation polish that makes everything feel "smooth".

### UI wire-up

1. **Header** — sync dot reads `store.syncStatus`; active sprint pill +
   mini progress reads `store.sprints[currentId]`; notifications bell
   surfaces escalations from `store.sprints` (filter for `failed`,
   `merge_blocked`, `needs_human_review`, `awaiting_signoff`).
2. **Right rail** — Cost Timeline block reads `store.project_metrics`;
   sync section reads `store.syncStatus`; activity ticker reads the last
   N transitions (`store.transitions`, maintained from state-update events).
3. **Dashboard** — hero stats + sprint list driven by `store.project_metrics`
   + `store.sprints`; inbox sidecar reads `store.feedback_inbox` (a new
   slice that polls `docs/feedback/inbox.md` on a slow cadence — 30s; not
   SSE-driven).
4. **Sprint view** — stepper + aux strip from `store.sprints[id]`; lane
   layout + slice cards from `store.slices`.
5. **Slice drawer** — every field reads from `store.slices[id]`.
6. **Settings → Health** — replace fixture data with `store.aggregates_history`
   (a new slice; aggregator periodically dumps to `state/aggregates-history.jsonl`
   for the 30d trend graphs — implemented here).

### Animation polish

Implement the full Q8 smoothness table:

| Element | Animation |
|---|---|
| State pill color | 400ms tween (CSS `transition`) |
| State pill text | cross-fade 200ms |
| Card border stripe | 400ms tween |
| Pulse dot | stop on state change, restart on new state |
| Card re-order | **FLIP technique** 300ms ease-out |
| Activity ticker entry | slide-in 250ms from top |
| Numeric counter | counter-tween from slice-12, 600ms |
| Progress bar width | 400ms ease |
| Sync dot color | 200ms tween |
| Slow/lost timestamps | desaturate 600ms |

The FLIP reorder is the trickiest — when a slice transitions and the sort
key changes, capture its bounding box before and after the DOM move, then
animate the inverse-then-play. Use a small util (`lib/flip.ts`).

### 4 sync-dot states

Right rail sync section + 18px collapsed strip must visually distinguish:
- **LIVE** — green dot, calm 2s pulse
- **SLOW** — amber dot, no pulse, "Synced 8s ago" timestamp
- **LOST** — red dot, fast 0.7s pulse, "Sync lost · retrying…"
- **CONNECTING** — grey dot, 1.2s pulse, "reconnecting attempt N/∞"

When sync goes SLOW or LOST, also desaturate every "Ns ago" timestamp on
the page (visceral "not live" signal).

## Acceptance criteria

- [ ] Starting a real sprint with `naml run` shows live state in the cockpit
- [ ] Every numeric ticks up smoothly when its underlying value changes
- [ ] Slice cards reorder smoothly (no jump) when state changes
- [ ] Sync dot reflects all 4 states correctly
- [ ] Killing the server triggers LOST after 15s; restarting reconnects + recovers
- [ ] All animations respect `prefers-reduced-motion`
- [ ] Sprint view + Dashboard + Settings → Health all render live data
- [ ] All gates pass; end-to-end test runs a fixture sprint and snapshots
      the cockpit UI at 5 points

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q8-sync.html (smoothness rules table)
- ../artifacts/mockups/q7b-metrics-hybrid.html (rail + hero binding)

## Constraints

- **All animations CSS-driven** where possible. JS only for FLIP + counter-tween.
- **No jumpy DOM mutations.** If a slice card moves from lane-2 to lane-3,
  FLIP it. If a numeric changes from $0.41 to $0.43, tween it.
- **Subscriptions are fine-grained.** A `metric-tick` for slice-4 should
  not re-render slice-7's card. Use the per-key subscribe pattern from
  slice-11.

## Notes from grilling session

This is the slice that decides whether the cockpit feels "alive" or "alive
with bugs". Spend disproportionate time here. The mockup is the bar; if
something in the live UI feels worse than the static mockup, fix it
before shipping.

Activity ticker is the user's confidence-check that the cockpit is wired:
when a state transitions, the ticker prepends a line. Don't ship without
that loop working end-to-end.
