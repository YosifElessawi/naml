# slice-4: Sprint state stepper + aux strip

## What to build

The sprint state machine visualization (Q4 Option A) — the cockpit centerpiece
on the Sprint view.

1. **Horizontal pill stepper** — 6 pills in a rail:
   `Planning → Publishing → Executing → Awaiting sign-off → Merging → Complete`.
   Each pill:
   - Circular dot (28px) with state number / checkmark
   - Label underneath
   - Duration line underneath (`12s`, `4s`, `21m · ETA 14m`, `—`)
   - Done state: solid green dot · solid green connector
   - Current state: cyan pulsing dot + glow ring + cyan connector before · grey after
   - Future state: grey dot · grey connector
2. **Slice sub-rail inside the `executing` pill.** A row of 5px-tall cells
   underneath the duration line, one per slice, colored by slice state.
3. **Aux strip** below the stepper — full-width row with monospace stats:
   `elapsed · ETA · cost · proj total · lanes 3/3 · slices 5/8 · retries · tier-1 so far · LGTM so far · last-tick`.
4. **Alternate exits** muted strip below aux — shows `partial_failure`,
   `failed`, `merge_blocked` as small dots labeled. Light up only when hit.

All data dummy. Component lives at `web/src/views/Sprint/Stepper/`.

## Acceptance criteria

- [ ] Stepper matches `mockups/q4-sprint-state.html` Option A
- [ ] Pulsing animation on current state dot (CSS, 2s ease-in-out)
- [ ] Sub-rail inside `executing` renders 8 cells with mixed states
- [ ] Aux strip wraps gracefully on narrow viewports
- [ ] When sprint is in `awaiting_signoff`, the Awaiting pill becomes the
      current state (pulse moves)
- [ ] When sprint hits `partial_failure`, the alternate-exits chip lights amber
- [ ] All `web-*` gates pass

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q4-sprint-state.html

## Constraints

- ETA derives from `mean per-slice duration so far × remaining slices`
  (Q4 decision). Computed in the store, not the component. For this slice,
  fixture the ETA value.
- Aux strip uses monospace font. Bold numbers, dim labels.
- Stepper connector line height is 2px, top:14px (centered on the 28px dot).

## Notes from grilling session

This is the cockpit's centerpiece. If the user can read sprint state in
< 1 second from this stepper, the rest of the cockpit succeeds. Spend
extra time on the pulse animation timing — it should feel calm, not anxious.
