// Activity ticker store slice.
//
// State-update events feed `recordTransition`. The ticker holds the most-
// recent N entries (capped by TRANSITIONS_CAP) so the right rail can
// render a slide-in animation when a new one lands.

import type { Store } from "./store.ts";
import { TRANSITIONS_CAP, type Transition } from "./types.ts";

let counter = 0;

/** Generate a monotonic, process-unique transition id. */
function makeId(): string {
  counter += 1;
  return `t-${Date.now().toString(36)}-${counter.toString(36)}`;
}

export function recordTransition(store: Store, input: Omit<Transition, "id">): Transition {
  const transition: Transition = { id: makeId(), ...input };
  const next = [transition, ...store.getState().transitions].slice(0, TRANSITIONS_CAP);
  store.patch("transitions", next);
  return transition;
}

export function clearTransitions(store: Store): void {
  store.patch("transitions", []);
}
