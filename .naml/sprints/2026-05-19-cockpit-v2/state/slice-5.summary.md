# slice-5 — Lane layout hybrid (Q6 Hybrid 2)

## What changed
- New `web/src/views/Sprint/Lanes/` directory containing:
  - `Lanes.tsx` — pure presentational React component, default-exports a
    `<Lanes />` element with a `data?: LanesData` prop and a fixture fallback.
  - `Lanes.css` — plain CSS (no CSS modules) port of the mockup styling.
    All design tokens use `var(--token, literal-fallback)` so the component
    renders even before slice-2's `tokens.css` is imported. Module-local
    `naml-lanes-pulse` keyframes; respects `prefers-reduced-motion`.
  - `types.ts` — wire shape: `LanesData`, `LaneTrack`, `LaneCard`,
    `GanttBlock`, `GanttGhost`, `GanttReadyBand`, `QueueChip`, `LaneStatus`.
  - `fixture.ts` — `lanesFixture` matching the Hybrid 2 mockup exactly
    (3 lanes: lane-1 review, lane-2 work@73% ctx warn, lane-3 idle).
  - `index.ts` — barrel re-exporting `Lanes`, `LanesProps`, `lanesFixture`,
    and the type surface.
  - `Lanes.test.tsx` — covers every acceptance criterion: time axis,
    swimlanes, now-line span, block colors, idle stripe + ready band,
    kanban pills + ctx-warn at ≥70%, queue chip kinds, summary, prop override.

## Non-obvious decisions
- **Hybrid 2, not Hybrid 1.** Spec called for the split (gantt + kanban +
  queue) layout. The fat-gutter Hybrid 1 is not built.
- **Plain CSS, no CSS modules.** Slice-4 added a `web/src/css-modules.d.ts`
  for `*.module.css` imports; that file isn't in this slice's worktree
  (upstream deps arrive only as summaries, not code), so this slice uses
  side-effect CSS imports to stay self-contained at gate time. All
  classnames are namespaced under `naml-lanes__*` to avoid collisions.
- **Component is pure-presentational.** No timers, no fetch — `data` prop
  drives everything. When SSE lands (slice-11/13), the store should
  produce a `LanesData` and pass it in directly; no refactor required.
- **CTX warn threshold = 70%.** Per spec ("amber at 70%, red at 85%").
  Currently amber-only; the red-step can be added later by extending
  `LaneCard` (e.g., a separate threshold) without touching consumers.
- **Time axis is fixed-window.** Component takes pre-computed `timeAxis`
  strings + `nowPct`; computation lives in the store, per Q6b decision.
- **Reflow at <900px** collapses the kanban row to a single column and
  stacks the queue strip vertically.

## For downstream slices
- Import via the barrel:
  `import { Lanes, lanesFixture, type LanesData } from "@/views/Sprint/Lanes"`
  (or relative). Component is not yet mounted in `App.tsx`; sprint route
  mount lands in slice-3.
- `LanesData` is the wire shape — when the SSE pipeline produces lane
  telemetry, it should emit this exact object so the component is a
  drop-in.
- Lane status enum is `LaneStatus = "work" | "review" | "idle"`. If more
  states appear (e.g., `held`, `failed`), add them in `types.ts` and
  extend the `.naml-lanes__card--*` / `.naml-lanes__pill--*` blocks in
  `Lanes.css`.
- The component owns its own `naml-lanes-pulse` keyframes; it does not
  depend on slice-2's global `.naml-pulse`.
