// Selector + hook for slice-8's Settings → Health page.
//
// Reads `store.aggregates_history` (populated by
// `store/aggregates-history.ts` via `/api/aggregates-history`) and
// returns the chart-friendly shape the existing Health view expects.
//
// At merge time, slice-8's `Health.tsx` replaces its `FIXTURE_HEALTH`
// import with `useHealthData()`.

import { useEffect, useState } from "react";

import { startAggregatesHistoryPoll } from "../aggregates-history.ts";
import { type Store, store as defaultStore } from "../store.ts";
import type { AggregatesHistoryPoint, NamlStore } from "../types.ts";

/** Structural mirror of slice-8's `HealthData`. */
export interface HealthData {
  costSeries: Array<{ day: string; value: number }>;
  tokensSeries: Array<{ day: string; value: number }>;
  sprintsStartedSeries: Array<{ day: string; value: number }>;
  sprintsCompletedSeries: Array<{ day: string; value: number }>;
  tier1HitSeries: Array<{ day: string; value: number }>;
  sliceFailSeries: Array<{ day: string; value: number }>;
  lastFetchedAt: string | null;
}

export function selectHealthData(state: NamlStore): HealthData {
  const points = state.aggregates_history.points;
  return {
    costSeries: series(points, (p) => p.cost_usd),
    tokensSeries: series(points, (p) => p.tokens),
    sprintsStartedSeries: series(points, (p) => p.sprints_started),
    sprintsCompletedSeries: series(points, (p) => p.sprints_completed),
    tier1HitSeries: series(points, (p) => p.tier1_hit_count),
    sliceFailSeries: series(points, (p) => p.slice_fails),
    lastFetchedAt: state.aggregates_history.lastFetchedAt,
  };
}

function series(
  points: AggregatesHistoryPoint[],
  pick: (p: AggregatesHistoryPoint) => number,
): Array<{ day: string; value: number }> {
  return points.map((p) => ({ day: p.day, value: pick(p) }));
}

/**
 * Hook that subscribes to the aggregates_history slice AND, if asked,
 * boots the poller on mount. The poller is opt-in so the slice-7 drawer
 * and others that consume the store don't trigger duplicate fetches.
 */
export function useHealthData(opts: { store?: Store; autoFetch?: boolean } = {}): HealthData {
  const store = opts.store ?? defaultStore;
  const autoFetch = opts.autoFetch ?? true;
  const [snap, setSnap] = useState<HealthData>(() => selectHealthData(store.getState()));

  useEffect(() => {
    const pull = () => setSnap(selectHealthData(store.getState()));
    const off = store.subscribeKey("aggregates_history", pull);
    pull();
    const poller = autoFetch ? startAggregatesHistoryPoll(store) : null;
    return () => {
      off();
      poller?.stop();
    };
  }, [store, autoFetch]);
  return snap;
}
