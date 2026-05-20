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
- Tests: 7 store/sync vitest suites (36 vitest pass), 3 new Python
  unittest modules (24 new tests, 276 total pass).
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
