# slice-3: Dashboard view — hero card + sprint timeline + feedback inbox sidecar

## What to build

The Dashboard view (default route `/dashboard`). Three regions:

1. **Project hero card** (full width, top). Matches Q3 Option A:
   - Project name + state pill (`ACTIVE` with pulsing dot)
   - Sub-line: `3 lanes · main · 14 sprints shipped · last merge 22m ago`
   - 4-stat row right-aligned: today $ · tokens today · tier-1 hit % · avg
     sprint duration (use the Q7b primary cost cluster instead if both fit —
     today / this week / 30d / lifetime). Per the grilling decision, lead with
     the **Q7b primary cost cluster** (4 big numbers) and use the secondary
     health row underneath for tier-1 / avg sprint / sprints-wk / etc.
2. **Sprint timeline** (left ~2/3 of main pane below hero). Grouped:
   - `ACTIVE (1)` — full sprint row with mini state-strip + state pill + meta
   - `QUEUED (N)` — smaller rows
   - `RECENT (last 3)` — for context
   - Each sprint row is clickable → navigates to `/sprint/<id>`
3. **Feedback inbox sidecar** (right ~1/3 of main pane below hero):
   - Count badge (`4 unfiled`, amber if > 3)
   - 3 most-recent bullets with source line
   - `/grill-feedback →` link (placeholder; the skill is CLI-only)

All data is dummy — load from a static `web/src/fixtures/dashboard.json` so
the shape is settled and slice-13 just swaps the source for SSE store.

## Acceptance criteria

- [ ] Hero card matches Q3 + Q7b hero recommendation visually
- [ ] Sprint timeline groups (Active / Queued / Recent) render with correct
      ordering + state pills
- [ ] Mini state-strip on each sprint row colors each cell by slice state
      (use `--c-work`, `--c-pr`, `--c-review`, `--c-merged`, `--c-blocked`, etc.)
- [ ] Clicking a sprint row navigates to `/sprint/<id>` (placeholder route OK)
- [ ] Inbox sidecar count badge turns amber when count > 3
- [ ] Layout collapses gracefully on viewport < 1100px (sidecar tucks below)
- [ ] All `web-*` gates pass

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q3-dashboard.html
- ../artifacts/mockups/q7b-metrics-hybrid.html

## Constraints

- Use the same dummy `dashboard.json` fixture in tests so the rendering is
  deterministic.
- Sprint row mini state-strip cells: width 14px, height 5px, gap 2px (port
  from `.smini i` in cockpit.css).
- The "primary cost cluster" in the hero is **4 stats with big numbers**
  (Q7b). The secondary 6-stat health row goes underneath. Tertiary 6-stat
  row can collapse-by-default.

## Notes from grilling session

The hero is the "project pulse" view — the user reads this in < 1 second to
answer "what's happening here?". Test that visual scanning works by deliberately
overloading the hero with content and pruning until it reads instantly.

The inbox sidecar is what keeps feedback from rotting. Don't bury it in a tab.
