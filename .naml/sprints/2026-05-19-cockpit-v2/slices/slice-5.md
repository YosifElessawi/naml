# slice-5: Lane layout hybrid — gantt + kanban current state + queue strip

## What to build

The lane layout (Q6 Hybrid 2). Three stacked regions under the sprint state
stepper on the Sprint view:

1. **Compact gantt** (top, ~120px tall):
   - Time axis at top (`14:02 · 14:08 · 14:14 · 14:20 · now · eta`)
   - 3 horizontal swimlanes (one per lane: lane-1, lane-2, lane-3)
   - Slice blocks sized by their duration. Three colors: `work` (cyan, glowing),
     `review` (amber), `merged` (green)
   - Ghost blocks past the "now" line — dashed border, dimmer, label `slice-N`
   - "Now" line — 2px cyan vertical bar with `NOW` label at top
   - Idle lane track uses a 45° striped background
2. **Kanban row** (middle, ~140px tall):
   - 3 cards equal width, one per lane
   - Each card shows lane name + state pill (BUSY · WORK / REVIEW / IDLE),
     current slice ID + title, live `DUR` and `CTX` meters with bars
   - Idle lane card shows `⌁ READY · NEXT SLICE WILL LAND HERE` dashed placeholder
3. **Queue strip** (bottom, ~40px tall):
   - Pill chips for queued slices: `slice-5 blocked on slice-4`, `slice-6 ready → lane-1`, etc.
   - Right-aligned summary: `3 lanes · 2 ready · 2 blocked · 1 idle`

All dummy data from fixtures. Component lives at `web/src/views/Sprint/Lanes/`.

## Acceptance criteria

- [ ] Gantt matches `mockups/q6b-lane-hybrids.html` Hybrid 2's top section
- [ ] Kanban row matches Hybrid 2's bottom section
- [ ] Idle lane's gantt track uses 45° stripe; kanban card shows dashed placeholder
- [ ] "Now" line spans all 3 swimlanes; renders with cyan glow
- [ ] Ghost blocks past now are visually dimmer than live blocks
- [ ] Queue chips colored: blocked = neutral, ready = green-tinged
- [ ] Component reflows to single-column at < 900px width
- [ ] All `web-*` gates pass

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q6b-lane-hybrids.html
- ../artifacts/mockups/q6-lanes.html

## Constraints

- **Time axis is fixed-window** — last 30 minutes + 6 minutes of future
  (Q6b decision). Auto-fit for short sprints comes in a follow-up sprint.
- **Idle "READY" band** glows only when there are ready slices queued
  (Q6b decision — pulse-when-actionable). When the queue is empty, idle
  lanes are calm grey-striped.
- **Lane count default 3** but the component must handle 1–6 gracefully.
  Past 4 lanes, shrink columns. Past 6, scroll horizontally.

## Notes from grilling session

The hybrid is what makes lane utilization the visual headline (an idle lane
during a busy sprint looks like wasted capacity). Get the idle-band glow
right — it's the "wake up, give me work" signal.

The kanban row is redundant-by-design with the gantt — that's the point.
Top = history/time, bottom = live state. Two lenses, one screen.
