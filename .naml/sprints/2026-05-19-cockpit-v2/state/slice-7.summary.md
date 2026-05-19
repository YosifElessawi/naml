# slice-7 — Slice detail drawer + Open-in-terminal gating

## What changed
- New `web/src/components/SliceDrawer/` directory:
  - `SliceDrawer.tsx` — default `<SliceDrawer data onClose onIntervene? />`.
    Right-side drawer (55% width, full width below 1024px) with backdrop
    blur, ESC / click-outside / × close affordances, action bar, gating
    note, overview grid, AC checklist, timeline (with `GATE FAIL` badge +
    retry/current markers), gate-output `<pre>`, auto-review verdict,
    and grilling notes.
  - `SliceDrawer.css` — plain CSS port of Q10 Option A mockup. Tokens
    use `var(--token, literal)` fallbacks so it renders before
    `tokens.css` is imported. Honors `prefers-reduced-motion`.
  - `types.ts` — `SliceDrawerData`, all 11 `SliceState`s, `ReviewVerdict`,
    `TokenCounts`, `ContextMeter`, `DurationMeter`, `TraversalStep`,
    `AcceptanceItem`, `GateOutput`, `ReviewDetail`, `InterveneResult`.
  - `format.ts` — `TERMINAL_LOCKED_STATES` / `TERMINAL_UNLOCKED_STATES`,
    `isTerminalLocked`, `isRestingState`, `contextSeverity` (70 amber,
    85 red), `stateLabel`, `formatPct`, `formatUsd`, `formatTokensShort`,
    `shortenSessionId`.
  - `fixtures.ts` — `drawerFixturesByState` covering all 11 states.
  - `useDrawerUrl.ts` — drawer state lives in `?drawer=<slice-id>` via
    `history.replaceState`. Returns `{ sliceId, open, close }`.
  - `index.ts` — barrel exports.
  - `SliceDrawer.test.tsx` + `useDrawerUrl.test.ts` — exhaustive vitest
    coverage of locking, gating note, HOLD POST, flash, close
    affordances, every-state-renders, and URL hook behaviour.
- `web/src/App.tsx` mounts the drawer with state-button demo triggers
  (one per state) so the drawer is reachable in the dev server until
  slice-3 (Sprint route) and slice-14 wire it from real slice cards.
- `naml/server.py`: added `POST /intervene/{slice_id}?action=` handling
  `hold | fail | skip | open-terminal`. `hold/fail/skip` return a 200
  stub (real state transition lands in slice-14). `open-terminal` shells
  out via `open -a Terminal` + `osascript` to run `claude --resume` at
  the slice's worktree (macOS only; 202 noop elsewhere).
- `web/vite.config.ts`: added `/intervene` to the dev proxy targets.
- `tests/test_server.py`: new `IntervenePostTests` covers all four
  actions, 400 on unknown/missing action, and the open-terminal route
  shape.

## Non-obvious decisions
- **Self-contained types.** I defined `SliceDrawerData` in
  `SliceDrawer/types.ts` rather than importing slice-6's `SliceCardData`.
  Slice-6 lives in a sibling worktree at implementation time, so the
  orchestrator's merge composes both — keeping each component's data
  contract local keeps the merge trivial. Downstream callers can adapt
  card data → drawer data (or unify the two) once both branches land.
- **HOLD button is disabled in resting states.** The slice prompt notes
  that HOLD should be "hidden or disabled" once the slice is already at
  rest. I kept the button visible but `disabled` so layout doesn't shift
  and the affordance still telegraphs the safe-intervention path.
- **Pending state is locked.** The spec only enumerates `setup · work ·
  pr` for the lock, but `pending` has no session yet — also locked.
  Same lock UX (tooltip + padlock).
- **Lock tooltip is state-aware.** Reads
  "Slice is in {STATE} · resuming the session in a terminal would
  interrupt naml's run. HOLD first." — the mockup hard-codes "WORK" but
  pr/setup are equally locked.
- **`open-terminal` POSTs from the UI** even when locked is false, so
  the Python side owns the shell-out. The button is the same `<button>`
  as HOLD/FAIL/SKIP — just routes through `onIntervene` → server.
- **No body-scroll lock** when drawer is open. The overlay is
  `position: fixed`; users can still scroll the underlying cockpit via
  the dim area. The drawer body has `overflow-y: auto`.
- **biome-ignore on the overlay onClick** is the only suppression —
  keyboard exit is ESC + the focusable × button, so the backdrop is
  intentionally mouse-only.

## For downstream slices
- Import the drawer via the barrel:
  ```ts
  import {
    SliceDrawer,
    useDrawerUrl,
    drawerFixturesByState,
    isTerminalLocked,
    type SliceDrawerData,
    type SliceState,
    type InterveneAction,
  } from "@/components/SliceDrawer"; // or relative
  ```
- Slice-5 / Slice-3 should call `open(sliceId)` from `useDrawerUrl()`
  on the slice card's "OPEN ↗" button. The URL param keeps the drawer
  open across refreshes.
- Slice-14 wires real semantics into `POST /intervene/{id}?action=hold`
  — flip state machine to `held`, drain in-flight turn, then the drawer
  re-opens unlocked because `isTerminalLocked("held") === false`.
- Slice-11/12 SSE telemetry should produce a `SliceDrawerData` (or feed
  a thin adapter) — the drawer is pure-presentational; no fetching of
  its own.
- The `view-pr` button uses `window.open(prUrl, "_blank")` and is
  disabled until `data.prUrl` is set.
- A demo `App.tsx` triggers were added for slice-7's dogfooding —
  slice-3 / slice-5 will replace them with the real sprint route.
