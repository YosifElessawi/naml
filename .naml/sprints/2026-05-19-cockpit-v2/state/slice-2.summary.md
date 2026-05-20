# slice-2 — cockpit shell: header + right rail + main-pane routing + design tokens

## What changed
- `web/src/styles/tokens.css` — ported verbatim from
  `artifacts/mockups/cockpit.css`. Holds the dark palette, state-machine
  colors (`--c-work`, `--c-pr`, `--c-review`, `--c-merged`, `--c-failed`,
  `--c-blocked`, `--c-human`), accents (`--c-accent` cyan, `--c-accent-2`
  green), warn/danger, radii, fonts. Defines `.naml-pulse` + a
  `prefers-reduced-motion` block that kills animations globally.
- `web/src/router/router.ts` + tests — plain React `useRoute()` hook
  backed by hash routing. Parses `/dashboard`, `/sprint/:id`,
  `/settings/:tab`, `/playground`; unknown paths fall back to dashboard.
- `web/src/components/Header/` — Q2 Option A header: logo, sync dot
  (CSS-pulsed), project switcher, active-sprint pill with mini progress
  bar, tab group (Dashboard/Sprint/Settings) wired through `onTabChange`,
  notifications bell, account chip, gear. All static dummy data.
- `web/src/components/RightRail/` — Q1 + Q7b right rail. Sections: sync +
  activity ticker (3 dummy entries), Cost Timeline block (today / week /
  30d / lifetime + sparkline), session quota, weekly quota, per-project
  share rollups. Collapses to an 18px strip via `RightRail collapsed`
  that keeps the pulsing sync dot visible (color reflects sync state).
- `web/src/components/Shell/` — CSS-grid 3-zone layout (`header / main /
  rail`), `⌘\` / `Ctrl+\` toggle for the rail, header tabs drive route
  state, route placeholders for Dashboard / Sprint / Settings.
- `web/src/playground/` — `/playground` route renders Header + RightRail
  (expanded + collapsed) in isolation with sync-state switches.
- `web/src/App.tsx` switches on the route between Shell and Playground;
  `main.tsx` now imports `styles/tokens.css` (legacy `styles.css` removed).

## Non-obvious decisions
- **Hash-based router, no library.** Keeps the bundle small and means
  aiohttp can serve `index.html` from a single mount point — no
  server-side rewrites needed when slice-11 adds SSE.
- **Sync state is a 4-value enum (`live / slow / lost / connecting`)**
  driven by a `SyncState` prop on `RightRail`. The collapsed icon-strip
  uses the same enum so the 18px dot color reflects connection health
  even when the rail is hidden — matches the spec note in slice-2.md.
- **CSS Modules per component** (filename `*.module.css`) — global CSS
  is reserved for `tokens.css`. Pulse animation is global because it's
  applied via the `.naml-pulse` class from any component.
- **Tab-to-route mapping fakes a sprint id (`current`)** until slice-4
  introduces a real active-sprint selector; the header pill is a static
  string for the same reason.

## Notes for downstream slices
- Tokens live at `web/src/styles/tokens.css`. Import once (already done
  in `main.tsx`); reach for the variables in any `*.module.css`.
- The route enum is `Route` in `web/src/router/router.ts`. Add new
  variants there + extend `parsePath` + `routeToPath`.
- The Dashboard placeholder is `Shell.MainPane.case "dashboard"` — that's
  where slice-3 hangs the hero / cost cluster / health row.
- `RightRail` accepts `syncState` + `activity` props; SSE plumbing in
  slice-11/13 should drive those rather than the defaults.
- `⌘\` keydown handler lives in `Shell.tsx`. If slice-3+ adds more
  shortcuts, consider lifting to a shared hook then.
- Playground is at `#/playground` — handy for tuning visual fidelity
  without booting the full app.
