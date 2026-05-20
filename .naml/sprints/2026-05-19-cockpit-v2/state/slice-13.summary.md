# slice-13 — Wire UI to live data + smoothness animations + 4 sync-dot states

## What changed

- **`web/src/store/`** — central pubsub `Store` with fine-grained keyed
  subscriptions (`subscribeKey("syncStatus", …)`, `slice:<id>`, etc.), the
  canonical `NamlStore` shape (sprints / slices / aggregates / transitions
  / feedback_inbox / aggregates_history / syncStatus), plus three slice-13
  store modules: `transitions.ts` (capped activity ticker),
  `feedback-inbox.ts` (30s poll of `/api/feedback-inbox`), and
  `aggregates-history.ts` (fetch + reducer for the Health 30d graphs).
- **`web/src/sync/EventSource.ts`** — SSE client that consumes `snapshot`
  / `state-update` / `metric-tick` / `ping` events from slice-11's
  `/events` route and patches the store. Owns the 4-state sync
  derivation (LIVE / SLOW @ 5s / LOST @ 15s / CONNECTING) and the
  reconnect-attempt counter.
- **`web/src/lib/flip.ts`** — FLIP utility (300ms ease-out) for smooth
  slice-card reorders. Respects `prefers-reduced-motion`.
- **`web/src/lib/tween.ts`** — rAF-based counter tween (600ms ease-out
  default) shipped here so slice-13 works in isolation; merges
  idempotently with slice-12's version.
- **`web/src/styles/animations.css`** — global Q8 smoothness rules
  (state pill 400ms, ticker slide-in 250ms, sync-dot 200ms, progress
  bar 400ms, `[data-sync-scope]` desaturate, 4 sync-dot keyframes), all
  neutralised under `prefers-reduced-motion`.
- **`web/src/components/SyncDot/`** — `<SyncDot>` (full + `compact`
  18px-strip variant) with the four state labels: "Synced Ns ago",
  "Sync lost · retrying…", "reconnecting attempt N/∞". Subscribes via
  the keyed pubsub so it only re-renders when sync state changes.
- **Python — `naml/feedback_inbox.py`** — markdown parser for
  `docs/feedback/inbox.md` (heading-aware, skips `## Filed`, captures
  `(from …)` source + `(yyyy-mm-dd)` date prefixes, stable SHA1-derived
  ids for dedupe).
- **Python — `naml/aggregates_history.py`** — JSONL daily-rollup format,
  `read_history`, `append_point`, atomic `write_or_replace_today`, and a
  `build_response` wire helper.
- **`naml/server.py`** — added two new routes:
  `GET /api/feedback-inbox` and `GET /api/aggregates-history?days=N`.
- **`web/src/components/ActivityTicker/`** — right-rail React component
  that subscribes to `store.transitions`, renders newest-first with the
  `.naml-anim-ticker-entry` slide-in class, and surfaces an "Ns ago"
  age label.
- **`web/src/components/NotificationsBell/`** — header bell that filters
  `store.sprints` + `store.slices` for the four escalation states
  (`failed`, `merge_blocked`, `awaiting_signoff`, `needs_human_review`),
  exposes a popover dialog, and exports `selectEscalations` as a pure
  selector.
- **`web/src/components/CostTimeline/`** — right-rail four-window cost
  block driven by `store.project_metrics`; each counter is animated by
  `lib/tween` with cleanup on unmount, lifetime cell tinted via
  `data-lifetime="true"`.
- **`web/src/components/SliceQueue/`** — slice-priority queue strip
  wrapped in `<FlipList>`. The post-commit FLIP pattern lives in
  `FlipList`, satisfying the "card re-order: FLIP 300ms ease-out"
  smoothness row without any render-phase side effect.
- **`web/src/components/FlipList/`** — drop-in container that
  FLIP-animates its keyed children on every commit via the new
  `flip.snapshot(root)` / `flip.playFromSnapshot(root, prev)` API
  driven from a `useLayoutEffect`. Slice-5's lane card list wraps in
  this at merge time.
- **`web/src/store/views/`** — six adapter hooks + matching pure
  selectors that bridge the live store to the data shapes slices 3–8
  already consume:
  `useDashboardData` / `useSprintData` / `useLanesData` /
  `useSliceCardData` / `useSliceDrawerData` / `useHealthData`.
  Each hook subscribes only to the store keys that affect its
  particular view, so a `metric-tick` for slice-4 still leaves
  slice-7's drawer alone.
- **`web/src/test/cockpit.e2e.test.tsx`** — end-to-end cockpit walk that
  drives `SseClient` through a fixture sprint (`connect → snapshot →
  state-change → escalation → recovery`) and snapshots a DOM fingerprint
  at all 5 points, asserting sync state, cost numbers, queue ordering,
  notifications bell label, and activity ticker contents.
- **`naml/feedback_inbox.py`** — recency ordering tightened to a single
  total order (dated DESC → undated in file order) with stable file-index
  tiebreakers, so polls don't reshuffle.
- Tests: 16 vitest suites + 1 cockpit e2e (73 vitest pass total), 3 new
  Python unittest modules (24 new tests, 276 total pass).
- **`web/vite.config.ts`** — corrected the slice-1 type error so
  `tsc --noEmit` passes; declares the `test` block via an explicit
  intersection type since vite@6 / vitest@2 type packages disagree.

## Non-obvious decisions

- **Slices 2–8 and 11–12 are not in this worktree.** The orchestrator
  composes them at merge time. Everything here is additive — when the
  merge lands, slice-3's `<Dashboard>` swaps its fixture for
  `store.subscribeKey("project_metrics", …)`, slice-8's `Health` swaps
  `FIXTURE_HEALTH` for `store.aggregates_history`, slice-2's header
  mounts `<SyncDot />`, slice-5's `<Lanes>` wraps slice cards in a
  `Flip` capture/play.
- **`<output>` for the sync dot.** Biome's a11y rule wanted a semantic
  element over `role="status"`; `<output>` implicitly has `role="status"`
  and screen-reader behaviour is unchanged.
- **Per-key subscriptions are mandatory.** A `metric-tick` for slice-4
  must not re-render slice-7's card; the `subscribeKey(sliceKey(id), …)`
  pattern is the contract downstream components consume.
- **Feedback inbox is poll-only.** 30s cadence, `lastPolledAt` recorded
  for the desaturate label rule; not SSE-driven per the slice spec.
- **Aggregates history rewrites today's row atomically** so multiple
  same-day writes don't double-count.

## For downstream slices

- **slice-14** can read `data-sync` attributes for the desaturate scope
  and consume the same store keys; `useDrawerUrl`'s drawer can listen
  via `subscribeKey(sliceKey(id), …)`.
- Activity ticker entries are in `store.transitions[]` (newest first,
  capped at `TRANSITIONS_CAP`). UI should iterate with the
  `.naml-anim-ticker-entry` class for slide-in on every new item.
- `Flip` is intentionally low-level — call `flip.capture(root)` before
  the React reconciliation and `flip.play(root)` in a `useLayoutEffect`
  after. The util reads `prefers-reduced-motion` automatically.
- The Python aggregator (slice-12) should call
  `aggregates_history.write_or_replace_today(...)` once per minute or on
  every state transition (whichever's cheaper) so the Health page sees
  fresh data.

## Merge-time wiring recipe — DO BEFORE CLOSING THE SPRINT

The orchestrator merges files; it does NOT rewrite imports. The
adapter hooks in `web/src/store/views/` are slice-13's answer to "make
the actual views live", but somebody has to flip the imports inside
the merged view files. The list below is exhaustive — each item is a
one-line swap.

| File | Replace | With |
|---|---|---|
| `web/src/components/Dashboard/Dashboard.tsx` | `import dashboardFixture from "../../fixtures/dashboard.json"` and any `data={dashboardFixture}` default | `import { useDashboardData } from "../../store/views"`; call `const data = useDashboardData()` |
| `web/src/views/Sprint/Stepper/Stepper.tsx` | `import { stepperFixture } from "./fixture"` default arg | `import { useSprintData } from "../../../store/views"`; call `const data = useSprintData(sprintId)` |
| `web/src/views/Sprint/Lanes/Lanes.tsx` | `import { lanesFixture } from "./fixture"` default arg | `import { useLanesData } from "../../../store/views"`; call `const data = useLanesData(sprintId)` |
| `web/src/views/Sprint/Lanes/Lanes.tsx` (the lane card list) | the inline `.map()` over lane cards | wrap in `<FlipList keyAttr="sliceId">…</FlipList>` from `web/src/components/FlipList` |
| `web/src/components/SliceCard/*` | `sliceCardFixturesByState[...]` | `import { useSliceCardData } from "../../store/views"`; call `const data = useSliceCardData(sliceId)` |
| `web/src/components/SliceDrawer/SliceDrawer.tsx` | `drawerFixturesByState[...]` | `import { useSliceDrawerData } from "../../store/views"`; call `const data = useSliceDrawerData(sliceId)` |
| `web/src/views/Health/Health.tsx` | `import { FIXTURE_HEALTH } from "./fixtures"` | `import { useHealthData } from "../../store/views"`; call `const data = useHealthData()` |
| `web/src/shell/Header.tsx` (or wherever slice-2's header sits) | n/a | mount `<SyncDot />` + `<NotificationsBell />` from `web/src/components/*` |
| `web/src/shell/RightRail.tsx` | n/a | mount `<CostTimeline />` + `<ActivityTicker />` from `web/src/components/*` |

The data shapes returned by each hook are structural mirrors of the
upstream `*Data` interfaces. If a downstream type drifts, fix the
selector first (it's a single file, no cascading change). The hooks
return `null` for unknown ids so the existing fixture loaders' "no
data" code paths still work — components that gate on falsy data
remain compatible.

## Acceptance criteria — status

- [x] Slice cards reorder smoothly: `SliceQueue` proves the pattern;
      slice-5's `Lanes` gets it free by wrapping in `<FlipList>` at
      merge time.
- [x] Sync dot reflects all 4 states (LIVE / SLOW / LOST / CONNECTING),
      now correctly holding CONNECTING until the first event arrives.
- [x] All animations respect `prefers-reduced-motion`.
- [x] E2E test walks a fixture sprint and snapshots the cockpit at 5
      points. (jsdom-driven against the full SseClient → Store →
      component graph; a Playwright variant is a follow-up if visual
      regressions become a recurring failure mode.)
- [⇢] "Starting a real sprint with `naml run` shows live state in the
      cockpit" + "Sprint view + Dashboard + Settings → Health all
      render live data" — *gated on the merge-time wiring recipe
      above*. The infrastructure is ready; flipping seven imports is
      the work left.
- [⇢] "Killing the server triggers LOST after 15s; restarting
      reconnects + recovers" — covered by `SseClient` unit tests but
      not by a process-level integration test. The browser-side
      reconnect is `EventSource`-native; the recovery happens at
      `markEvent()` time.
