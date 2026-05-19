# slice-6 — Slice cards (Q5 Option A)

## What changed
- New `web/src/components/SliceCard/` directory containing:
  - `SliceCard.tsx` — default-exports `<SliceCard data variant>` with two
    variants (`full` and `compact`). Pure-presentational React component.
  - `SliceCard.css` — plain CSS port of the Q5 Option A mockup. All design
    tokens use `var(--token, literal-fallback)` so the card renders before
    slice-2's `tokens.css` is imported. Classnames namespaced under
    `naml-scard__*`. Module-local `naml-scard-pulse` and `naml-scard-flip`
    keyframes; respects `prefers-reduced-motion`.
  - `types.ts` — wire shape: `SliceCardData`, `SliceState` (all 11),
    `SliceKind`, `ReviewVerdict`, `TokenCounts`, `DurationMeter`,
    `ContextMeter`, `TraversalStep`, `ReviewDetail`.
  - `format.ts` — `formatDuration`, `formatUsd`, `formatTokensShort`,
    `contextSeverity` (70 amber / 85 red), `durationSeverity`,
    `shortenSessionId`, `stateLabel`.
  - `CounterTween.tsx` — RAF-based number tween (~400ms ease-out, reduced-
    motion safe). Built here per slice spec; slice-13 will reuse/polish.
  - `fixtures.ts` — `sliceCardFixturesByState` map covering all 11 state
    variants (pending, setup, work, pr, review, merged, failed,
    needs_human_review, blocked_upstream, abandoned, held).
  - `index.ts` — barrel re-exporting `SliceCard`, `SliceCardProps`, all
    types, formatters, `CounterTween`, and fixtures.
  - `SliceCard.test.tsx` — exercises every acceptance criterion: row1,
    dual meters, stats strip, session-id + actions, traversal chain, all
    11 state variants (data-state attribute + modifier-class assertion),
    context-window colour-flip (50/73/88), retry visibility (n=0 → hidden,
    n>0 → shown), card-flip on state change, compact variant.

## Non-obvious decisions
- **`held` state included now** even though the runtime gating fix lands
  in slice-14. The card renders an amber state stripe, "HELD" pill, and
  a "paused by user" banner (or `data.heldReason` if provided) so slice-14
  can wire data without touching the card.
- **Card-flip animation** is a single-element rotateX flash (300ms ease-out)
  toggled by an effect that watches `data.state`. True FLIP cross-DOM
  reorders happen at the list level (slice-5's Lanes), not here.
- **Assertion-based tests, not file snapshots.** Used inline structural
  assertions per state (data-state attribute + modifier class + state pill)
  rather than `toMatchSnapshot()` to avoid CI failures on missing snapshot
  files in fresh worktrees. Coverage still meets "snapshot test per state".
- **Counter tween reads previous value via ref** (not stale `display`
  closure) so biome's exhaustive-deps lint stays clean without
  `// biome-ignore` comments.
- **State-modifier classes** map `needs_human_review` → `needs-human-review`
  and `blocked_upstream` → `blocked-upstream`. State pill label folds
  `needs_human_review` → "HITL" and `blocked_upstream` → "BLOCKED" via
  `stateLabel()`; everything else is `state.toUpperCase()`.
- **Action buttons are bare buttons with `aria-label`s.** Wiring (copy
  session ID, open terminal, view branch) lands in slice-7's drawer +
  slice-14 (terminal gating). The buttons remain visible on the card so
  the gating layer can simply disable them based on state.

## For downstream slices
- Import via the barrel: `import { SliceCard, sliceCardFixturesByState,
  type SliceCardData } from "@/components/SliceCard"` (or relative).
  Not yet mounted in `App.tsx` — slice-3 wires the Sprint route, slice-5
  swaps its lane-kanban `.lcard` for `<SliceCard variant="compact" />`,
  slice-7 mounts the drawer trigger.
- When SSE telemetry (slice-11/12) emits per-slice events, produce a
  `SliceCardData` object and pass it in directly — no refactor required.
- The action buttons are non-interactive in this slice. Slice-7's drawer
  + slice-14's gating should consume `data.state` to enable/disable them
  (locked in setup/work/pr; unlocked in held/review/merged/failed/
  needs_human_review/abandoned).
