# slice-2: Cockpit shell — header + right rail + main-pane routing + design tokens

## What to build

Port the cockpit shell from the mockups into real React + CSS, with three
persistent zones and route-driven main pane.

1. **Design tokens.** Port `mockups/cockpit.css` into `web/src/styles/tokens.css`
   as CSS custom properties. Keep the dark cockpit aesthetic exactly as
   captured: color tokens for state machine (`--c-work`, `--c-pr`, `--c-review`,
   `--c-merged`, `--c-failed`, `--c-blocked`, `--c-human`), accent
   (`--c-accent` cyan, `--c-accent-2` green), and warn/danger.
2. **Header component** (Q2 Option A spec). Logo · sync dot · project switcher
   (static dropdown for now) · active-sprint pill (with mini progress bar) ·
   tabs (Dashboard / Sprint / Settings) · notifications bell · account chip
   · gear icon. All static; no live data.
3. **Right rail component** (Q1 + Q7 specs). Sectioned vertical layout:
   sync header · activity ticker (3 dummy entries) · **Cost Timeline block**
   (today / week / 30d / lifetime — placeholder $) · session quota bar ·
   weekly quota bar · per-project share rollups. All dummy data.
4. **Main pane router.** Three routes: `/dashboard`, `/sprint/:id`, `/settings/:tab`.
   Use plain React state for routing (no Router lib unless trivially needed).
   Default route is `/dashboard`. Tabs in the header drive the route.
5. **Layout.** CSS grid with three zones. Right rail collapsible to 18px
   icon-strip via `⌘\`. Reduce-motion media query disables animations.

## Acceptance criteria

- [ ] `pnpm --dir web build` produces a bundle that renders the shell
- [ ] Header matches `mockups/q2-header.html` Option A visually (same spacing,
      same chips, same hover states)
- [ ] Right rail matches `mockups/q1-shell.html` and the right-rail portion
      of `mockups/q7b-metrics-hybrid.html` Option A
- [ ] All three tabs in the header switch the main pane between Dashboard /
      Sprint / Settings (placeholder content per route — real views land in
      slices 3, 4–6, 8)
- [ ] `⌘\` toggles the right rail between full-width and 18px icon-strip
- [ ] Header sync dot pulses (CSS animation) — no real data yet
- [ ] Storybook entry (or a `web/src/playground/` route) renders each
      shell component in isolation
- [ ] All `web-*` gates pass

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q1-shell.html
- ../artifacts/mockups/q2-header.html
- ../artifacts/mockups/q7b-metrics-hybrid.html
- ../artifacts/mockups/cockpit.css

## Constraints

- **Match the mockup pixel-for-pixel** where possible. Where the mockup uses
  inline styles, port them to CSS-module rules in the component's directory.
- **No live data.** Everything is dummy. Slice-11 wires SSE; slice-13 wires
  the components to live data. This slice is pure shell.
- **CSS Modules** per component (no global stylesheets except `tokens.css`).
- **Accessibility:** keyboard focus visible on every interactive element.
  Tab order: header tabs → main pane → right rail.

## Notes from grilling session

The shell is what gives the cockpit its "alive" feel — sync dot pulses, hover
states crisp, transitions snappy. Don't ship this slice if any of those feel
janky compared to the mockup. The mockup is the bar.

The 18px collapsed right-rail strip should still show the sync dot color
(LIVE / SLOW / LOST / CONNECTING) so the user knows the cockpit is alive
even when they've reclaimed width.
