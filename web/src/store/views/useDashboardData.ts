// Selector + hook that turns the live store into the `DashboardData`
// shape slice-3's `<Dashboard />` already consumes.
//
// At merge time, slice-3's component file replaces its fixture import:
//
//     - import dashboardFixture from "../../fixtures/dashboard.json";
//     + import { useDashboardData } from "../../store/views/useDashboardData";
//
// and reads `useDashboardData()` instead of the JSON. The component's
// type contract (`DashboardData` from slice-3's `types.ts`) is the same;
// we re-declare a structural mirror here so the wiring can be exercised
// in isolation before slice-3 lands.

import { useEffect, useState } from "react";

import { type Store, store as defaultStore } from "../store.ts";
import type { NamlStore, ProjectMetrics, SliceState, SprintSummary } from "../types.ts";

/** Structural mirror of slice-3's `DashboardData`. Keep field names + shape
 *  in sync. */
export interface DashboardData {
  project: { name: string };
  cost: {
    today: number;
    week: number;
    last30d: number;
    lifetime: number;
  };
  health: {
    tier1HitPct: number | null;
    avgSprintMinutes: number | null;
    sprintsPerWeek: number | null;
    sliceFailRatePct: number | null;
    lgtmFirstPassPct: number | null;
    meanReviewIterations: number | null;
  };
  active: SprintRow[];
  queued: SprintRow[];
  recent: SprintRow[];
  inbox: { count: number };
}

export interface SprintRow {
  id: string;
  title: string;
  state: SprintSummary["state"];
  slicesDone: number;
  slicesTotal: number;
  cost: number;
  miniStrip: SliceState[]; // one entry per slice, colored by state
}

const NULL_HEALTH: DashboardData["health"] = {
  tier1HitPct: null,
  avgSprintMinutes: null,
  sprintsPerWeek: null,
  sliceFailRatePct: null,
  lgtmFirstPassPct: null,
  meanReviewIterations: null,
};

/**
 * Pure selector. Tests use this directly; the hook is a thin wrapper.
 * Splitting the function out keeps the data-shape contract auditable
 * without having to render a React component.
 */
export function selectDashboardData(state: NamlStore, projectName = "naml"): DashboardData {
  const sprints = Object.values(state.sprints);
  const rows: SprintRow[] = sprints.map((s) => ({
    id: s.id,
    title: s.title,
    state: s.state,
    slicesDone: s.slicesDone,
    slicesTotal: s.slicesTotal,
    cost: state.sprint_metrics[s.id]?.cost_usd ?? 0,
    miniStrip: collectMiniStrip(state, s.id),
  }));
  const active = rows.filter((r) => r.state === "executing");
  const queued = rows.filter((r) => r.state === "planning" || r.state === "publishing");
  const recent = rows
    .filter((r) => r.state === "complete" || r.state === "failed" || r.state === "merge_blocked")
    .slice(-3);
  return {
    project: { name: projectName },
    cost: costSnapshot(state.project_metrics),
    health: NULL_HEALTH,
    active,
    queued,
    recent,
    inbox: { count: state.feedback_inbox.unfiledCount },
  };
}

function costSnapshot(m: ProjectMetrics): DashboardData["cost"] {
  return {
    today: m.today_cost,
    week: m.week_cost,
    last30d: m.last_30d_cost,
    lifetime: m.lifetime_cost,
  };
}

function collectMiniStrip(state: NamlStore, sprintId: string): SliceState[] {
  return Object.values(state.slices)
    .filter((s) => s.sprintId === sprintId)
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((s) => s.state);
}

/** Subscribes to every key that affects the dashboard data shape, so the
 *  hook returns a fresh `DashboardData` whenever anything changes. */
export function useDashboardData(store: Store = defaultStore, projectName?: string): DashboardData {
  const [snap, setSnap] = useState<DashboardData>(() =>
    selectDashboardData(store.getState(), projectName),
  );
  useEffect(() => {
    const pull = () => setSnap(selectDashboardData(store.getState(), projectName));
    const offs = [
      store.subscribeKey("sprints", pull),
      store.subscribeKey("slices", pull),
      store.subscribeKey("project_metrics", pull),
      store.subscribeKey("sprint_metrics", pull),
      store.subscribeKey("feedback_inbox", pull),
    ];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store, projectName]);
  return snap;
}
