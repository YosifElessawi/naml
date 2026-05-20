// Slice queue strip.
//
// Renders every slice in the store as a small chip, sorted by a state-
// derived priority. When a slice transitions and its priority changes,
// the list re-orders — and `Flip` captures the bounding rects before the
// React commit, then plays the inverse-then-forward animation after. The
// "Card re-order: FLIP technique 300ms ease-out" row from the Q8
// smoothness table is satisfied by this component.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { Flip } from "../../lib/flip.ts";
import { type Store, store as defaultStore } from "../../store/store.ts";
import type { SliceState, SliceSummary } from "../../store/types.ts";

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
  const rootRef = useRef<HTMLUListElement | null>(null);
  const flipRef = useRef<Flip>(flip ?? new Flip((el) => el.dataset.sliceId));

  useEffect(() => {
    const pull = () => setBag(store.getState().slices);
    const off = store.subscribeKey("slices", pull);
    pull();
    return off;
  }, [store]);

  // Snapshot rects *before* React commits the next reorder so the play
  // call can animate the inverted offset.
  if (rootRef.current) flipRef.current.capture(rootRef.current);

  // `bag` is the dependency by design: the effect body doesn't *read* bag,
  // it re-fires after every commit-where-bag-changed so Flip.play() runs
  // against the just-reordered DOM. Biome's exhaustive-deps doesn't model
  // this "fire on change of unused value" pattern.
  // biome-ignore lint/correctness/useExhaustiveDependencies: see above
  useLayoutEffect(() => {
    if (rootRef.current) flipRef.current.play(rootRef.current);
  }, [bag]);

  const ordered = useMemo(() => sortSlices(bag), [bag]);

  return (
    <section className="naml-queue" aria-label="Slice queue">
      <header className="naml-queue__header">Queue</header>
      <ul className="naml-queue__list" ref={rootRef}>
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
      </ul>
    </section>
  );
}
