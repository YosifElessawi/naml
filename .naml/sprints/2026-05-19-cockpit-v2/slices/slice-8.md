# slice-8: Settings tabbed page + Health page with trend graphs

## What to build

Settings view (Q9 Option A) — left-nav tabbed page. Seven tabs:
**Project · Lanes · Gates · Account · Sync · Health · Advanced**.
"+ Add project…" anchored at the bottom of the nav (runs `/setup-naml`).

Component at `web/src/views/Settings/`. Each tab is a sub-component
at `web/src/views/Settings/<TabName>/`.

### Per-tab content (forms; bound to `.naml/config.toml` via existing
`naml.config` module):

- **Project** — repo slug · base branch · root path · ADR folder · lifetime
  tracking date · sprint+slice label prefixes · lifecycle label tag-list
- **Lanes** — parallel-lane slider (1–8, default 3) · DAG-width detection
  toggle · hard cap
- **Gates** — ordered list editor (lint, typecheck, test, build); each row =
  name + argv input + last-run status (✓ / ▶ running / ✗)
- **Account** — `CLAUDE_CONFIG_DIR` dropdown · model dropdown · context-window
  (read-only) · plan caps + reset anchors
- **Sync** — SSE heartbeat seconds · sync thresholds (SLOW/LOST) · reduce-motion toggle
- **Health** — separate sub-view with trend graphs (see below)
- **Advanced** — feature flags · JSONL compaction trigger · "Reset aggregates"
  button · "Wipe sprint history" (destructive, double-confirm) · raw
  `config.toml` editor

### Health page (the trend graphs)

Six trend cards in a 3-column grid (Q9 Health tab spec):
1. **Daily cost · 30d** — line chart of daily $ over last 30 days
2. **Tier-1 hit · 30d** — line chart of rolling tier-1 %
3. **Avg sprint duration · 30d** — line chart of p50 sprint duration
4. **Slice fail rate · 7d** — line chart, amber threshold at 5%
5. **Review LGTM 1st pass** — line chart of % over 30d
6. **Per-skill cost · 30d** — horizontal bar chart (implement / review / merge / grill)

Plus a follow-up section: per-state retry breakdown · reviewer/implementer
disagreement rate · TTFP histogram.

All data comes from the aggregator (slice-10 + slice-12). For this slice,
use fixtures; the wire-up to live data happens in slice-13.

### Save behaviour

Autosave on field blur. Show a `Saved 2s ago` badge near the section header.
No modal Save button. Writes go through a server endpoint (`POST /config`)
that updates `.naml/config.toml` atomically.

## Acceptance criteria

- [ ] All 7 tabs render and switch correctly
- [ ] Project tab loads from `.naml/config.toml` (via server endpoint
      `GET /config`) and saves field changes on blur
- [ ] Lanes slider snaps to integer values 1–8; default 3
- [ ] Gates tab adds/removes rows; cheapest-first ordering enforced
- [ ] Health tab renders all 6 trend charts (use lightweight SVG, not a
      charting library) with axes + legend
- [ ] Trend chart values come from `/aggregates` fixture for now
- [ ] Reset Aggregates button confirms once and triggers `POST /aggregates/reset`
      (server handler in slice-10)
- [ ] Layout matches `mockups/q9-settings.html` Option A
- [ ] All `web-*` gates pass

## Artifacts

- ../artifacts/spec.md
- ../artifacts/mockups/q9-settings.html

## Constraints

- **No charting library**. Use raw SVG `<polyline>` and `<rect>` — the mockup
  proves it works. Bundles smaller, dep-free, easier to style.
- **Atomic config writes**: the server must rewrite `config.toml` atomically
  (write to temp + rename) to avoid corruption during a write race.
- **Field validation**: empty repo slug or invalid model name should prevent
  save; show inline error.
- **Health page graphs** use `cockpit-accent` for primary line, `cockpit-warn`
  for amber-threshold ranges. Animations on hover (datapoint tooltip).

## Notes from grilling session

Settings is the calm view — you visit it to *change* something, not to
*monitor*. Health is the exception — it's the analytics dashboard, and it
earns the most generous canvas. Don't crowd it with config inputs.

Autosave + the "Saved 2s ago" badge replaces the modal Save dance. It's the
right pattern for a single-user local tool.
