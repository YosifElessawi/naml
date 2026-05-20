# slice-4 — Sprint state stepper + aux strip

## What changed
- `web/src/views/Sprint/Stepper/Stepper.tsx` — Q4 Option A horizontal pill
  stepper. 6 pills (`planning → publishing → executing → awaiting_signoff →
  merging → complete`), each with 28px dot, label, duration line, and
  connector. Done = solid green, current = pulsing cyan + glow, future = grey.
- `Stepper.module.css` — pixel-faithful port of the mockup styling, with
  local `@keyframes pulse` (2s ease-in-out infinite) and a
  `prefers-reduced-motion` block. Uses `var(--c-accent, …)` etc. with fallback
  literals so the component renders even before `tokens.css` is loaded.
- Sub-rail inside the `executing` pill — renders one 11×5px cell per slice,
  colored by slice state (`merged / review / work / pr / failed / ...`).
  Wraps when narrow.
- Aux stat strip — monospace row with elapsed / ETA / cost / proj total /
  lanes / slices / retries / tier-1 / LGTM / last-tick. `flex-wrap` so it
  collapses gracefully on narrow viewports. Bold numbers, dim labels.
- Alternate-exits strip — dashed muted row with three dots
  (`partial_failure → awaiting_signoff`, `failed`, `merge_blocked → human`).
  Each dot has a `data-lit` attribute driven by `hitExits[]`; lit dots get
  the spec colors (amber / red / orange).
- `types.ts`, `fixture.ts`, `index.ts` — typed model
  (`SprintState`, `SliceState`, `AlternateExit`, `AuxStats`, `StepperData`),
  fixture data matching the mockup, and a barrel export.
- `Stepper.test.tsx` — covers all six acceptance criteria: pill rendering,
  pulse on current, 8-cell sub-rail with mixed states, aux values present,
  pulse moves to `awaiting_signoff`, partial_failure lights the chip.
- `web/src/css-modules.d.ts` — wildcard declaration so `tsc --noEmit`
  accepts `*.module.css` imports.

## Non-obvious decisions
- **All ETA / duration values are fixtured.** Per slice constraint, the
  computation (`mean per-slice duration × remaining slices`) lives in the
  store, not the component. Component is pure-presentational and takes a
  `StepperData` prop with a `stepperFixture` default.
- **`SPRINT_STATES` is the canonical ordering.** Computed `currentIdx` via
  `indexOf`; status derived as done/current/future. When state is
  `complete`, all 6 pills are marked done.
- **`pulse` keyframes are module-local, not global.** Slice-2's
  `tokens.css` defines `.naml-pulse`; this component owns its own keyframes
  so it's portable and tests don't depend on global styles.
- **CSS variables have inline fallbacks** (`var(--c-accent, #38bdf8)`) so
  the stepper still renders if mounted before slice-2's `tokens.css` is
  imported.
- **Sub-rail wraps with `flex-wrap`** rather than truncating — keeps every
  slice cell visible even when there are many slices in a tight pill.

## For downstream slices
- Import from the barrel: `import { Stepper, stepperFixture } from
  "@/views/Sprint/Stepper"` (or relative). Pass `data` to drive it from
  the store; default fixture is good enough for empty / loading states.
- `StepperData` is the wire shape — when the SSE pipeline (slice-11/13)
  lands, the store should produce this exact object so the component
  doesn't need refactoring.
- Slice state machine is `SliceState` in `types.ts`. Add cells/colors there
  if more states appear later — `Stepper.module.css` has a class per state.
- The component is self-contained — nothing in `App.tsx` was wired up.
  Slice-3 (sprint route mount) is where this hangs in the route tree.
