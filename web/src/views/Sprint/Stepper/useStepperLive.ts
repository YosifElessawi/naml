// Live-store translator for `<Stepper />`. Maps the slim store shape
// (NamlStore.sprints/slices + sprint_metrics) into the StepperData
// presentation shape the slice-4 component already consumes.
//
// Picks the "current" sprint by most-recently-updated (matches the
// Header's selection) so dashboard + stepper always agree.

import { useEffect, useState } from "react";

import { type Store, store as defaultStore } from "../../../store/store.ts";
import type {
  SliceState as DomainSliceState,
  SprintState as DomainSprintState,
  NamlStore,
  SliceSummary,
  SprintSummary,
} from "../../../store/types.ts";
import type {
  AlternateExit,
  AuxStats,
  SliceState as PresentationSliceState,
  SprintState as PresentationSprintState,
  StepperData,
} from "./types.ts";

const STATE_TO_PILL: Record<DomainSliceState, PresentationSliceState> = {
  pending: "idle",
  setup: "setup",
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

// The store's SprintState is a superset of the stepper's. The
// stepper has six canonical states; failed/merge_blocked are surfaced
// as `hitExits`, not as a current-state pill.
const SPRINT_TO_STEPPER: Record<DomainSprintState, PresentationSprintState> = {
  planning: "planning",
  publishing: "publishing",
  executing: "executing",
  awaiting_signoff: "awaiting_signoff",
  merging: "merging",
  complete: "complete",
  failed: "executing", // exit chip lights; pill stays where work was
  merge_blocked: "merging",
};

function fmtCost(usd: number): string {
  return usd >= 100 ? `$${usd.toFixed(0)}` : `$${usd.toFixed(2)}`;
}

function fmtLastTick(iso: string | null): string {
  if (!iso) return "—";
  const dt = new Date(iso);
  const sec = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  return `${Math.floor(min / 60)}h ago`;
}

function pickCurrentSprint(state: NamlStore): SprintSummary | null {
  const sprints = Object.values(state.sprints);
  if (sprints.length === 0) return null;
  return sprints.slice().sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))[0] ?? null;
}

function exitsFromState(s: DomainSprintState): AlternateExit[] {
  if (s === "failed") return ["failed"];
  if (s === "merge_blocked") return ["merge_blocked"];
  return [];
}

export function selectStepperLive(state: NamlStore): StepperData | null {
  const sprint = pickCurrentSprint(state);
  if (!sprint) return null;
  const sliceList: SliceSummary[] = Object.values(state.slices)
    .filter((s) => s.sprintId === sprint.id)
    .sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
  const metrics = state.sprint_metrics[sprint.id];
  const aux: AuxStats = {
    elapsed: "—",
    eta: "—",
    cost: fmtCost(metrics?.cost_usd ?? 0),
    projTotal: fmtCost(state.project_metrics.lifetime_cost),
    lanes: "—",
    slices: `${sprint.slicesDone} / ${sprint.slicesTotal}`,
    tier1: metrics?.tier1_hit_count ? String(metrics.tier1_hit_count) : undefined,
    lgtm: metrics?.lgtm_first_pass_count ? String(metrics.lgtm_first_pass_count) : undefined,
    lastTick: fmtLastTick(state.lastEventAt),
  };
  return {
    currentState: SPRINT_TO_STEPPER[sprint.state] ?? "planning",
    durations: {},
    sliceStates: sliceList.map((s) => STATE_TO_PILL[s.state] ?? "idle"),
    aux,
    hitExits: exitsFromState(sprint.state),
  };
}

export function useStepperLive(store: Store = defaultStore): StepperData | null {
  const [snap, setSnap] = useState<StepperData | null>(() => selectStepperLive(store.getState()));
  useEffect(() => {
    const pull = () => setSnap(selectStepperLive(store.getState()));
    const offs = [
      store.subscribeKey("sprints", pull),
      store.subscribeKey("slices", pull),
      store.subscribeKey("sprint_metrics", pull),
      store.subscribeKey("project_metrics", pull),
      store.subscribeKey("lastEventAt", pull),
    ];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store]);
  return snap;
}
