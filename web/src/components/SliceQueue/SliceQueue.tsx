// Slice queue strip.
//
// Renders every slice in the store as a small chip, sorted by a state-
// derived priority. When a slice transitions and its priority changes,
// the list re-orders — `FlipList` wraps the chip list and animates each
// chip from its prior position via a post-commit `useLayoutEffect`, so
// there are no render-phase side effects (StrictMode-safe, concurrent-
// rendering-safe).

import { useEffect, useMemo, useState } from "react";

import type { Flip } from "../../lib/flip.ts";
import { type Store, store as defaultStore } from "../../store/store.ts";
import type { SliceState, SliceSummary } from "../../store/types.ts";
import { FlipList } from "../FlipList/FlipList.tsx";

// Lower priority sorts to the front. Resting states sink. Tuned to match
// the V2 spec's "queue strip lists ready / blocked slices" ordering.
const PRIORITY: Record<SliceState, number> = {
  needs_human_review: 0,
  failed: 1,
  blocked_upstream: 2,
  held: 3,
  review: 4,
  pr: 5,
  work: 6,
  setup: 7,
  pending: 8,
  merged: 9,
  abandoned: 10,
};

function sortSlices(bag: Record<string, SliceSummary>): SliceSummary[] {
  return Object.values(bag).sort((a, b) => {
    const pa = PRIORITY[a.state] ?? 99;
    const pb = PRIORITY[b.state] ?? 99;
    if (pa !== pb) return pa - pb;
    return a.id.localeCompare(b.id);
  });
}

interface SliceQueueProps {
  store?: Store;
  /** Override the FLIP utility — exposed so tests can disable motion. */
  flip?: Flip;
}

export function SliceQueue({ store = defaultStore, flip }: SliceQueueProps) {
  const [bag, setBag] = useState<Record<string, SliceSummary>>(() => store.getState().slices);

  useEffect(() => {
    const pull = () => setBag(store.getState().slices);
    const off = store.subscribeKey("slices", pull);
    pull();
    return off;
  }, [store]);

  const ordered = useMemo(() => sortSlices(bag), [bag]);

  return (
    <section className="naml-queue" aria-label="Slice queue">
      <header className="naml-queue__header">Queue</header>
      <FlipList flip={flip} className="naml-queue__list" ariaLabel="Slice queue items">
        {ordered.map((s) => (
          <li
            key={s.id}
            data-slice-id={s.id}
            data-state={s.state}
            className="naml-queue__chip naml-anim-card-stripe"
          >
            <span className="naml-queue__id">{s.id}</span>
            <span className="naml-queue__state naml-anim-state-pill">{s.state.toUpperCase()}</span>
          </li>
        ))}
      </FlipList>
    </section>
  );
}
