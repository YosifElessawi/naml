# slice-3 — Dashboard view: hero card + sprint timeline + feedback inbox sidecar

## What changed
- `web/src/components/Dashboard/Dashboard.tsx` — Dashboard view per Q3 (A) +
  Q7b hero recommendation. Three regions:
  - **Hero**: project name + pulsing state pill + sub-line; Q7b primary cost
    cluster (4 stats: today / this week / 30d / lifetime, lifetime tinted
    cyan); secondary 6-stat health row; collapsible-by-default tertiary
    6-stat row (rendered at 0.85 opacity).
  - **Sprint timeline** (`<div className={styles.timeline}>`): three groups
    `ACTIVE / QUEUED / RECENT` as `role="heading" aria-level={2}` rows, each
    followed by `SprintRow` buttons.
  - **Inbox sidecar** (`<aside aria-label="Feedback inbox">`): count badge,
    three bullets, `/grill-feedback →` placeholder button.
- `web/src/components/Dashboard/Dashboard.module.css` — all styling for the
  Dashboard. References slice-2's `tokens.css` variables (`--c-work`,
  `--c-pr`, `--c-review`, `--c-merged`, `--c-failed`, `--c-blocked`,
  `--c-human`, `--c-accent`, `--c-warn`, etc.) with literal hex fallbacks so
  the component still renders in isolation. Mini state-strip cells are
  **14×5px, 2px gap** per the slice spec (mockup shows 6px height; spec
  override applied). `@media (max-width: 1100px)` collapses the sidecar
  below the timeline; the primary cost cluster reflows 4→2 columns and the
  health row reflows 6→3 columns there.
- `web/src/components/Dashboard/types.ts` — `DashboardData` and child types
  (`SprintRowData`, `CostSlice`, `HealthStat`, `InboxBlock`,
  `SliceStateColor`, `SprintPillState`).
- `web/src/fixtures/dashboard.json` — deterministic fixture used both by
  the component and by the tests. Mirror of the Q3 + Q7b mockup data
  (`inpolicy/web`, 4-unfiled inbox to exercise the amber count badge, 1
  active / 2 queued / 3 recent sprints, an 8-cell mini state-strip on the
  active row).
- `web/src/components/Dashboard/Dashboard.test.tsx` — 8 vitest specs
  covering hero, primary cost cluster, secondary health row, sprint
  grouping/counts, mini state-strip cells, navigation, amber count badge,
  inbox bullet rendering.
- `web/src/App.tsx` + `App.test.tsx` — App now renders `<Dashboard />`
  directly. App.test.tsx asserts the Dashboard region is present.
- `web/src/vite-env.d.ts` — declares `*.module.css` so the TypeScript
  strict config (`noUncheckedIndexedAccess`) doesn't poison dotted access
  on the styles object. Typed `any` with a `biome-ignore` and an inline
  rationale.

## Non-obvious decisions
- **Slice-2 not yet merged into this worktree.** Slice-3 imports the
  Dashboard from `./components/Dashboard/Dashboard`; once slice-2 lands,
  its `Shell.MainPane` `case "dashboard"` should render `<Dashboard />`
  instead of the placeholder (slice-2 set up that seam). My `App.tsx`
  edit is the in-worktree wiring that lets `web-test` and `web-build`
  pass here; the orchestrator's merge of slice-2's `App.tsx`/`Shell.tsx`
  takes precedence at sprint merge time.
- **Hash-routing nav, no library.** `openSprint(id)` sets
  `window.location.hash = "#/sprint/<id>"` — compatible with slice-2's
  `parsePath`. Tests assert `window.location.hash` directly, which keeps
  the test independent of the (still-unmerged) router module.
- **Amber-count threshold = `> 3`** — matches the slice acceptance
  criterion. Surface it through a `data-warn` attribute so tests don't
  depend on CSS-module class hashing in the jsdom environment.
- **Sprint rows are `<button>`s with `aria-label="Open sprint <title>"`**
  so assistive tech / tests can find them by name, and keyboard
  navigation Just Works.
- **Tertiary health row stays expanded.** Q7b explicitly OK'd this; the
  toggle is deferred to a later slice.

## Notes for downstream slices
- `<Dashboard />` is the route target for `/dashboard`. Downstream Shell
  should render it via `<Shell.MainPane case="dashboard"><Dashboard /></...>`
  (or whatever slice-2's mainpane API ended up looking like).
- `web/src/fixtures/dashboard.json` is the canonical shape. **Slice-13
  (SSE store) should swap the fixture import for a store hook** with the
  same shape — types in `Dashboard/types.ts` are the contract.
- Slice cards in slice-5 should reuse `SliceStateColor` for mini-cell
  colors and the same `--c-*` tokens for state pills.
- Navigation is hash-based (`#/sprint/<id>`); downstream sprint view
  (slice-4) should read the id from `window.location.hash` or from
  slice-2's `useRoute()` once the router lands.
- The Dashboard component imports `./Dashboard.module.css` with classes
  typed via `vite-env.d.ts`. If slice-2 also adds the same declaration,
  the merge is idempotent; otherwise this file is the source of truth.
