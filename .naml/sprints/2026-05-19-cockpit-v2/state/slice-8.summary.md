# slice-8 — Settings tabbed page + Health page with trend graphs

## What changed
- `web/src/views/Settings/Settings.tsx` — root tabbed page (Q9 Option A).
  Seven left-nav tabs: **Project · Lanes · Gates · Account · Sync · Health
  · Advanced**, plus an "+ Add project…" entry pinned at the bottom (emits
  a `naml:add-project` custom event the Shell can listen for to spawn the
  `/setup-naml` CLI skill). Accepts a `tab` prop so slice-2's router can
  drive the active tab from `#/settings/:tab`; internal state is the
  fallback so the component works standalone in `<Settings />`.
- Tab sub-components — one folder per tab, each one a thin form that takes
  `value`, `onChange`, `onCommit` and validates inline. Shared widgets
  (Panel/Field/TextInput/SelectInput/Toggle/Slider/TagList/StatusPill/
  Button/IconButton/SavedBadge) live at
  `web/src/views/Settings/components/widgets.tsx` and pull color from
  the slice-2 design tokens with safe inline fallbacks.
- `web/src/views/Settings/Gates/Gates.tsx` — cheapest-first ordering is
  enforced on blur via `sortGatesByCost` (lint → typecheck → test →
  build; `web-*` variants ranked alongside). Add/remove rows; argv is
  free-text and passed verbatim to the lane subprocess.
- `web/src/views/Settings/useAutosave.ts` — accumulates a pending
  partial diff per section; `flush()` (called from each tab's onBlur)
  POSTs to `/config` and updates the `Saved Ns ago` badge.
- `web/src/views/Settings/config-client.ts` — `loadSettings()` /
  `saveSettings()` / `resetAggregates()`. All three swallow fetch
  failures and fall back to `FIXTURE_SETTINGS` so the UI is usable
  before slice-10 ships the real endpoints.
- `web/src/views/Health/Health.tsx` + `fixtures.ts` — 5 SVG line charts
  + 1 SVG bar chart in a responsive 3-column grid. Each chart is dep-free
  (`<polyline>` for lines, `<rect>` for bars) with hover dot + value
  tooltip. The slice-fail-rate card gets an amber band at the 5%
  threshold. A follow-up row shows retry breakdown, disagreement rate,
  and TTFP histogram.
- `web/src/views/Settings/Health/Health.tsx` — thin Settings tab
  wrapper that re-renders the canonical Health view.

## Non-obvious decisions
- **CSS Modules with inline fallbacks for design tokens.** Each
  component reads `var(--c-accent, #38bdf8)` etc. so the Settings/Health
  views render correctly even before slice-2's `tokens.css` is loaded
  (e.g. when tested in isolation or built standalone).
- **No charting library.** Per the slice constraint — raw `<polyline>`
  / `<rect>` over a fixed 220×48 viewport scaled with `preserveAspectRatio
  ="none"`. Same approach the mockup uses; ships ~zero bundle weight.
- **Autosave is per-section, not per-field.** Calling `updateSection`
  replaces the whole section in the pending diff. On blur, the entire
  pending bag flushes. Avoids per-keystroke POSTs without needing a
  debounce timer; safer because partial writes inside a section are
  never visible to the server.
- **Validation runs at render time, not on blur.** Repo slug / sync
  thresholds / model name show their error span as soon as the user
  introduces invalid input. Blur only gates whether the diff is
  committed (`commitIfValid`). Keeps the UI responsive while still
  preventing bad state from reaching `.naml/config.toml`.
- **Wipe sprint history is two-stage** (button → confirm panel →
  explicit "Confirm wipe"). The actual destructive endpoint is
  intentionally deferred — UI surfaces the button but does not yet
  call a server route, so an accidental wipe is impossible until
  slice-10+ wires it up behind real auth.
- **Settings is the controlled tab consumer.** The Shell from slice-2
  parses `#/settings/:tab` and passes `tab` as a prop; without the
  prop, internal state handles standalone use. `onTabChange` notifies
  the Shell so it can sync the URL.

## Notes for downstream slices
- **slice-13 (live wire-up):** replace the `FIXTURE_HEALTH` import in
  `web/src/views/Health/Health.tsx` with a fetch against
  `/aggregates` (slice-10 endpoint). The `Health` component already
  accepts a `data` prop, so the wiring layer can pass live data
  without touching the rendering.
- **slice-10 (server endpoints):** the client expects:
  - `GET /config` → JSON matching `NamlSettings` (mapping in
    `types.ts`); missing keys are filled from `FIXTURE_SETTINGS`.
  - `POST /config` → accepts a `Partial<NamlSettings>` body, returns
    `{ok: true}` on success. Server must rewrite `.naml/config.toml`
    atomically (write-temp + rename) per slice spec.
  - `POST /aggregates/reset` → no body, returns 200 on success.
- **slice-11 (Shell routing):** to wire Settings into the Shell, import
  `{ Settings, type TabId }` from `views/Settings/` and pass the parsed
  `tab` segment from `#/settings/:tab`. The Shell should also listen
  for `naml:add-project` `CustomEvent` to launch `/setup-naml`.
- The shared widgets in `components/widgets.tsx` are intentionally
  generic and can be lifted out to a top-level `web/src/components/`
  if other views need them; reach for them before re-implementing
  toggles or sliders elsewhere.
