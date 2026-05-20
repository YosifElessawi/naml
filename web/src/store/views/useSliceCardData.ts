// Selector + hook for slice-6's `<SliceCard data={...} />`. Returns the
// `SliceCardData` shape that slice-6 ships, sourcing live numbers from
// the store's slice + slice_metrics maps.
//
// At merge time, slice-6's slice-card renderer (currently fixture-driven)
// reads `useSliceCardData(sliceId)`.

import { useEffect, useState } from "react";

import { type Store, store as defaultStore, sliceKey } from "../store.ts";
import type { NamlStore, SliceState } from "../types.ts";

/** Structural mirror of slice-6's `SliceCardData`. */
export interface SliceCardData {
  id: string;
  title: string;
  state: SliceState;
  lane: number | null;
  sprintId: string;
  ctxPct: number;
  costUsd: number;
  tokensIn: number;
  tokensOut: number;
  cacheRead: number;
  cacheWrite: number;
  lastEventAt: string | null;
}

export function selectSliceCardData(state: NamlStore, sliceId: string): SliceCardData | null {
  const slice = state.slices[sliceId];
  if (!slice) return null;
  const m = state.slice_metrics[sliceId];
  return {
    id: slice.id,
    title: slice.title,
    state: slice.state,
    lane: slice.lane,
    sprintId: slice.sprintId,
    ctxPct: m?.ctx_pct ?? 0,
    costUsd: m?.cost_usd ?? 0,
    tokensIn: m?.tokens_in ?? 0,
    tokensOut: m?.tokens_out ?? 0,
    cacheRead: m?.cache_read ?? 0,
    cacheWrite: m?.cache_write ?? 0,
    lastEventAt: m?.last_event_at ?? null,
  };
}

export function useSliceCardData(
  sliceId: string,
  store: Store = defaultStore,
): SliceCardData | null {
  const [snap, setSnap] = useState<SliceCardData | null>(() =>
    selectSliceCardData(store.getState(), sliceId),
  );
  useEffect(() => {
    const pull = () => setSnap(selectSliceCardData(store.getState(), sliceId));
    // Fine-grained subscriptions: only re-render when *this* slice's row
    // or *this* slice's metrics change. The slice_metrics bag fans out
    // through patchSlice / patch, so subscribing to the bag is the
    // cheapest catch-all here.
    const offs = [
      store.subscribeKey(sliceKey(sliceId), pull),
      store.subscribeKey("slice_metrics", pull),
    ];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store, sliceId]);
  return snap;
}
