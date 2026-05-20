// Selector + hook for slice-7's `<SliceDrawer data={...} />`.
//
// Slice-7's `SliceDrawerData` is a superset of `SliceCardData`; we
// re-use the card hook's shape and add the fields the drawer needs.
// At merge time, slice-7's `useDrawerUrl` returns a slice id; the
// drawer reads `useSliceDrawerData(id)`.

import { useEffect, useState } from "react";

import { type Store, store as defaultStore, sliceKey } from "../store.ts";
import type { NamlStore, SliceState } from "../types.ts";

/** Structural mirror of slice-7's `SliceDrawerData`. */
export interface SliceDrawerData {
  id: string;
  title: string;
  state: SliceState;
  sprintId: string;
  lane: number | null;
  ctxPct: number;
  costUsd: number;
  tokensIn: number;
  tokensOut: number;
  cacheRead: number;
  cacheWrite: number;
  lastEventAt: string | null;
  /** Most-recent transitions FOR THIS SLICE; ordered newest-first. */
  history: Array<{ id: string; fromState: string | null; toState: string; at: string }>;
  /** Sync status mirrored here so the drawer can desaturate timestamps. */
  syncStatus: NamlStore["syncStatus"];
}

export function selectSliceDrawerData(state: NamlStore, sliceId: string): SliceDrawerData | null {
  const slice = state.slices[sliceId];
  if (!slice) return null;
  const m = state.slice_metrics[sliceId];
  const history = state.transitions
    .filter((t) => t.kind === "slice" && t.targetId === sliceId)
    .map((t) => ({ id: t.id, fromState: t.fromState, toState: t.toState, at: t.at }));
  return {
    id: slice.id,
    title: slice.title,
    state: slice.state,
    sprintId: slice.sprintId,
    lane: slice.lane,
    ctxPct: m?.ctx_pct ?? 0,
    costUsd: m?.cost_usd ?? 0,
    tokensIn: m?.tokens_in ?? 0,
    tokensOut: m?.tokens_out ?? 0,
    cacheRead: m?.cache_read ?? 0,
    cacheWrite: m?.cache_write ?? 0,
    lastEventAt: m?.last_event_at ?? null,
    history,
    syncStatus: state.syncStatus,
  };
}

export function useSliceDrawerData(
  sliceId: string,
  store: Store = defaultStore,
): SliceDrawerData | null {
  const [snap, setSnap] = useState<SliceDrawerData | null>(() =>
    selectSliceDrawerData(store.getState(), sliceId),
  );
  useEffect(() => {
    const pull = () => setSnap(selectSliceDrawerData(store.getState(), sliceId));
    const offs = [
      store.subscribeKey(sliceKey(sliceId), pull),
      store.subscribeKey("slice_metrics", pull),
      store.subscribeKey("transitions", pull),
      store.subscribeKey("syncStatus", pull),
    ];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store, sliceId]);
  return snap;
}
