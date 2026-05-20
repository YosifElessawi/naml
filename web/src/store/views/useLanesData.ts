// Selector + hook for slice-5's `<Lanes />` (`LanesData` shape).
//
// At merge time, slice-5's component file replaces `lanesFixture` with
// `useLanesData(sprintId)`.

import { useEffect, useState } from "react";

import { type Store, store as defaultStore } from "../store.ts";
import type { NamlStore, SliceState, SliceSummary } from "../types.ts";

/** Mirror of slice-5's `LanesData`. */
export interface LanesData {
  lanes: LaneTrack[];
  queue: SliceSummary[];
}

export interface LaneTrack {
  lane: number;
  status: "work" | "review" | "idle";
  currentSliceId: string | null;
  title: string | null;
  ctxPct: number;
}

export function selectLanesData(state: NamlStore, sprintId: string): LanesData {
  const sliceList = Object.values(state.slices).filter((s) => s.sprintId === sprintId);
  const byLane = new Map<number, SliceSummary[]>();
  for (const slice of sliceList) {
    if (slice.lane === null) continue;
    const list = byLane.get(slice.lane) ?? [];
    list.push(slice);
    byLane.set(slice.lane, list);
  }
  const lanes: LaneTrack[] = [];
  for (const [lane, group] of [...byLane.entries()].sort(([a], [b]) => a - b)) {
    const current = pickCurrent(group);
    const status = laneStatus(current?.state);
    lanes.push({
      lane,
      status,
      currentSliceId: current?.id ?? null,
      title: current?.title ?? null,
      ctxPct: current ? (state.slice_metrics[current.id]?.ctx_pct ?? 0) : 0,
    });
  }
  const queue = sliceList.filter((s) => s.lane === null);
  return { lanes, queue };
}

function pickCurrent(group: SliceSummary[]): SliceSummary | undefined {
  return (
    group.find((s) => s.state === "work" || s.state === "pr" || s.state === "review") ?? group[0]
  );
}

function laneStatus(state: SliceState | undefined): LaneTrack["status"] {
  if (!state) return "idle";
  if (state === "review" || state === "pr") return "review";
  if (state === "work" || state === "setup") return "work";
  return "idle";
}

export function useLanesData(sprintId: string, store: Store = defaultStore): LanesData {
  const [snap, setSnap] = useState<LanesData>(() => selectLanesData(store.getState(), sprintId));
  useEffect(() => {
    const pull = () => setSnap(selectLanesData(store.getState(), sprintId));
    const offs = [store.subscribeKey("slices", pull), store.subscribeKey("slice_metrics", pull)];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store, sprintId]);
  return snap;
}
