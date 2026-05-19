# slice-6: Slice cards — dense telemetry, all state variants

## What to build

The dense telemetry slice card from Q5 Option A. This is the most information-
rich surface in the cockpit. Reused inside lanes (slice-5's kanban row uses
the compact variant) and on the Sprint view's slice list.

Component at `web/src/components/SliceCard/`. Two variants:

1. **`SliceCard.full`** — ~110px tall, the variant used in the main slice list:
   - **Row 1:** slice ID chip · title · `AFK`/`HITL` badge · lane chip · state pill (pulsing dot)
   - **Row 2:** dual progress bars side-by-side
     - DURATION · IN STATE — current value + budget tick mark
     - CONTEXT WINDOW — % + raw tokens. **Amber 70%, red 85%**
   - **Row 3:** stats strip — cost · tokens (in/out/cache) · retry n/cap (hidden when 0) · last gate · spacer · session id with action buttons (`⎘ ▶ ⎇`)
   - Below: traversal pill chain `setup → work (retry 1/2) → pr → review → merged`
   - State stripe (3px) on left edge colored by state
2. **`SliceCard.compact`** — ~70px tall, used inside lane kanban cards (slice-5):
   - Row 1: slice ID + title + state pill
   - Row 2: just `DUR` + `CTX` meters (smaller)
   - Row 3: cost + retry (if > 0)

All state variants must render correctly: `pending · setup · work · pr · review · merged · failed · needs_human_review · blocked_upstream · abandoned · held`.

When state is `held`: state stripe is grey/amber, state pill says `HELD`, the
HOLD-related UI is shown (Pause icon + "paused by user" label). `held` is a
new slice state introduced for the gating fix; see slice-14.

## Acceptance criteria

- [ ] `SliceCard.full` matches `mockups/q5-slice-card.html` Option A
- [ ] All 11 state variants render correctly (snapshot tests for each)
- [ ] Context-window meter color flips: green &lt; 70%, amber 70-85%, red ≥ 85%
- [ ] Retry counter only renders when n > 0
- [ ] Session ID + action buttons line up to the right of the stats strip
- [ ] Card-flip animation on state change (FLIP technique, 300ms ease-out)
- [ ] `SliceCard.compact` matches Hybrid 2's lane kanban card
- [ ] All `web-*` gates pass; snapshot test suite includes each state variant

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q5-slice-card.html
- ../artifacts/mockups/q6b-lane-hybrids.html

## Constraints

- **Context-window thresholds**: 70% amber, 85% red. Both colors come from
  CSS tokens (`--c-warn`, `--c-failed`). Don't hardcode hex.
- **Duration budget tick** mark sourced from `state_budgets` table in
  the store (hardcoded defaults per state for now — Q5 micro-decision).
- **Numeric counters** must use the counter-tween component for animation
  (build it here if it doesn't exist yet — slice-13 polishes the spec).
- **Card width:** flexible. Full variant fits comfortably in 720px+;
  compact in 280px.

## Notes from grilling session

The context-window meter is the highest-value signal in the cockpit. Amber
at 70% means "this slice is getting big, watch it"; red at 85% means
"about to die, intervene now". Make sure the color change is impossible to miss.

Hide retry-count when 0 (avoids visual noise on healthy slices). Show it
prominently (warning color) when > 0 — retries are the first sign a slice
is struggling.
