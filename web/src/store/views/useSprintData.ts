// Selector + hook that produces the `StepperData` shape slice-4's
// `<Stepper />` consumes (plus the aux-strip stats next to it).
//
// At merge time, slice-4's component file replaces its `stepperFixture`
// import with `useSprintData(sprintId)`.

import { useEffect, useState } from "react";

import { type Store, store as defaultStore } from "../store.ts";
import type { NamlStore, SliceState, SprintState } from "../types.ts";

/** Mirror of slice-4's `StepperData` — keep field names + shape in sync. */
export interface StepperData {
  state: SprintState;
  /** Slice-state cells for the inner sub-rail. */
  miniStrip: SliceState[];
  aux: AuxStats;
}

export interface AuxStats {
  elapsedMs: number | null;
  etaMs: number | null;
  costUsd: number;
  projectTotalUsd: number;
  lanesUsed: number;
  lanesMax: number;
  slicesDone: number;
  slicesTotal: number;
  retries: number;
  tier1HitCount: number;
  lgtmCount: number;
  lastTickAt: string | null;
}

const EMPTY_AUX: AuxStats = {
  elapsedMs: null,
  etaMs: null,
  costUsd: 0,
  projectTotalUsd: 0,
  lanesUsed: 0,
  lanesMax: 3,
  slicesDone: 0,
  slicesTotal: 0,
  retries: 0,
  tier1HitCount: 0,
  lgtmCount: 0,
  lastTickAt: null,
};

export function selectSprintData(state: NamlStore, sprintId: string): StepperData | null {
  const sprint = state.sprints[sprintId];
  if (!sprint) return null;
  const sliceList = Object.values(state.slices)
    .filter((s) => s.sprintId === sprintId)
    .sort((a, b) => a.id.localeCompare(b.id));
  const metrics = state.sprint_metrics[sprintId];
  const aux: AuxStats = {
    ...EMPTY_AUX,
    costUsd: metrics?.cost_usd ?? 0,
    projectTotalUsd: state.project_metrics.lifetime_cost,
    slicesDone: sprint.slicesDone,
    slicesTotal: sprint.slicesTotal,
    tier1HitCount: metrics?.tier1_hit_count ?? 0,
    lgtmCount: metrics?.lgtm_first_pass_count ?? 0,
    lastTickAt: state.lastEventAt,
  };
  return {
    state: sprint.state,
    miniStrip: sliceList.map((s) => s.state),
    aux,
  };
}

export function useSprintData(sprintId: string, store: Store = defaultStore): StepperData | null {
  const [snap, setSnap] = useState<StepperData | null>(() =>
    selectSprintData(store.getState(), sprintId),
  );
  useEffect(() => {
    const pull = () => setSnap(selectSprintData(store.getState(), sprintId));
    const offs = [
      store.subscribeKey(`sprint:${sprintId}`, pull),
      store.subscribeKey("slices", pull),
      store.subscribeKey("sprint_metrics", pull),
      store.subscribeKey("project_metrics", pull),
      store.subscribeKey("lastEventAt", pull),
    ];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store, sprintId]);
  return snap;
}
