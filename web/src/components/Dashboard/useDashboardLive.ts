// Translates the live `Store` state into the rich `DashboardData` shape
// the slice-3 component already consumes. Lives next to the component
// (not in `store/views/`) because the mapping is presentation-specific
// — store/views/useDashboardData.ts intentionally returns a leaner
// domain shape, and we don't want to widen that contract for one view.

import { useEffect, useState } from "react";

import { type Store, store as defaultStore } from "../../store/store.ts";
import type {
  SliceState as DomainSliceState,
  SprintState as DomainSprintState,
  NamlStore,
  SliceSummary,
  SprintSummary,
} from "../../store/types.ts";
import type {
  DashboardData,
  HealthStat,
  ProjectMeta,
  SliceStateColor,
  SprintPillState,
  SprintRowData,
} from "./types";

// --- mapping tables ------------------------------------------------------

const STATE_TO_COLOR: Record<DomainSliceState, SliceStateColor> = {
  pending: "idle",
  setup: "work",
  work: "work",
  pr: "pr",
  review: "review",
  merged: "merged",
  failed: "failed",
  needs_human_review: "human",
  blocked_upstream: "blocked",
  abandoned: "failed",
  held: "human",
};

const SPRINT_STATE_TO_PILL: Record<DomainSprintState, SprintPillState> = {
  planning: "planned",
  publishing: "planned",
  executing: "executing",
  awaiting_signoff: "signoff",
  merging: "executing",
  complete: "complete",
  failed: "failed",
  merge_blocked: "failed",
};

const ACTIVE_STATES = new Set<DomainSprintState>([
  "executing",
  "publishing",
  "merging",
  "awaiting_signoff",
]);

const QUEUED_STATES = new Set<DomainSprintState>(["planning"]);

const RECENT_STATES = new Set<DomainSprintState>(["complete", "failed", "merge_blocked"]);

const PROJECT_NAME = "YosifElessawi/naml";
const PROJECT_SINCE = "2026-03-14";

// --- helpers -------------------------------------------------------------

function fmtUsd(n: number): string {
  return n >= 100 ? `$${n.toFixed(0)}` : `$${n.toFixed(2)}`;
}

function slicesByState(slices: SliceSummary[]): SliceStateColor[] {
  return slices
    .slice()
    .sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
    .map((s) => STATE_TO_COLOR[s.state] ?? "idle");
}

function buildSprintRow(
  sprint: SprintSummary,
  slices: SliceSummary[],
  state: NamlStore,
): SprintRowData {
  const cost = state.sprint_metrics[sprint.id]?.cost_usd ?? 0;
  const pill = SPRINT_STATE_TO_PILL[sprint.state] ?? "planned";
  const stateLabel = pill.toUpperCase();
  return {
    id: sprint.id,
    title: sprint.title || sprint.id,
    subtitle: `${sprint.slicesTotal} slices · ${sprint.state}`,
    state: pill,
    stateLabel,
    slices: slicesByState(slices),
    metaTopRight: `slices ${sprint.slicesDone}/${sprint.slicesTotal}`,
    metaBottomRight: cost > 0 ? `cost ${fmtUsd(cost)}` : undefined,
    openLabel: pill === "complete" || pill === "failed" ? "VIEW →" : "OPEN →",
  };
}

function buildProject(state: NamlStore): ProjectMeta {
  const sprints = Object.values(state.sprints);
  const sprintsShipped = sprints.filter((s) => s.state === "complete").length;
  const dailyBurn7d =
    state.aggregates_history.points.length > 0
      ? state.aggregates_history.points.slice(-7).reduce((acc, p) => acc + p.cost_usd, 0) /
        Math.min(7, state.aggregates_history.points.length)
      : 0;
  const anyExecuting = sprints.some((s) => ACTIVE_STATES.has(s.state));
  return {
    name: PROJECT_NAME,
    state: anyExecuting ? "ACTIVE" : "IDLE",
    branch: "main",
    lanes: 0,
    sprintsShipped,
    lastMergeMinutesAgo: 0,
    dailyBurn7dAvgDollars: dailyBurn7d,
  };
}

function buildHealth(state: NamlStore): HealthStat[] {
  const points = state.aggregates_history.points;
  const last7 = points.slice(-7);
  const tier1Total = last7.reduce((acc, p) => acc + p.tier1_hit_count, 0);
  const sprintsCompleted = last7.reduce((acc, p) => acc + p.sprints_completed, 0);
  const sliceFails = last7.reduce((acc, p) => acc + p.slice_fails, 0);
  return [
    {
      label: "TIER-1 · 7d",
      value: sprintsCompleted > 0 ? `${Math.round((tier1Total / sprintsCompleted) * 100)}%` : "—",
      tone: undefined,
    },
    {
      label: "SPRINTS / WK",
      value: String(last7.reduce((acc, p) => acc + p.sprints_started, 0)),
    },
    {
      label: "SLICE FAILS · 7d",
      value: String(sliceFails),
      tone: sliceFails > 0 ? "warn" : "ok",
    },
    {
      label: "ACTIVE SPRINTS",
      value: String(Object.values(state.sprints).filter((s) => ACTIVE_STATES.has(s.state)).length),
    },
  ];
}

function buildHealthTertiary(state: NamlStore): HealthStat[] {
  const m = state.project_metrics;
  return [
    { label: "TOKENS IN · 30d", value: m.tokens_in > 0 ? m.tokens_in.toLocaleString() : "—" },
    { label: "TOKENS OUT · 30d", value: m.tokens_out > 0 ? m.tokens_out.toLocaleString() : "—" },
    { label: "CACHE READ · 30d", value: m.cache_read > 0 ? m.cache_read.toLocaleString() : "—" },
    { label: "CACHE WRITE · 30d", value: m.cache_write > 0 ? m.cache_write.toLocaleString() : "—" },
  ];
}

// --- public API ----------------------------------------------------------

export function selectDashboardLive(state: NamlStore): DashboardData {
  const sprints = Object.values(state.sprints);
  const allSlices = Object.values(state.slices);
  // Build (sprint, row) pairs so the bucket filters below don't have to
  // re-index back into `sprints` (which makes biome itch about
  // non-null assertions).
  const pairs = sprints.map((s) => ({
    sprint: s,
    row: buildSprintRow(
      s,
      allSlices.filter((sl) => sl.sprintId === s.id),
      state,
    ),
  }));
  const active = pairs.filter((p) => ACTIVE_STATES.has(p.sprint.state)).map((p) => p.row);
  const queued = pairs.filter((p) => QUEUED_STATES.has(p.sprint.state)).map((p) => p.row);
  const recent = pairs
    .filter((p) => RECENT_STATES.has(p.sprint.state))
    .map((p) => p.row)
    .sort((a, b) => b.id.localeCompare(a.id))
    .slice(0, 3);
  return {
    project: buildProject(state),
    cost: {
      today: { dollars: state.project_metrics.today_cost },
      thisWeek: { dollars: state.project_metrics.week_cost },
      last30d: { dollars: state.project_metrics.last_30d_cost },
      lifetime: {
        dollars: state.project_metrics.lifetime_cost,
        since: PROJECT_SINCE,
      },
    },
    health: buildHealth(state),
    healthTertiary: buildHealthTertiary(state),
    sprints: { active, queued, recent },
    inbox: {
      unfiled: state.feedback_inbox.unfiledCount,
      link: "/grill-feedback →",
      bullets: state.feedback_inbox.bullets.slice(0, 3).map((b) => ({
        text: b.text,
        source: b.source,
      })),
    },
  };
}

export function useDashboardLive(store: Store = defaultStore): DashboardData {
  const [snap, setSnap] = useState<DashboardData>(() => selectDashboardLive(store.getState()));
  useEffect(() => {
    const pull = () => setSnap(selectDashboardLive(store.getState()));
    const offs = [
      store.subscribeKey("sprints", pull),
      store.subscribeKey("slices", pull),
      store.subscribeKey("project_metrics", pull),
      store.subscribeKey("sprint_metrics", pull),
      store.subscribeKey("feedback_inbox", pull),
      store.subscribeKey("aggregates_history", pull),
    ];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store]);
  return snap;
}
