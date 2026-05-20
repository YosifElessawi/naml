// Tiny pubsub store. Plain object + per-key listeners — no Redux, no Zustand.
//
// Fine-grained subscriptions are mandatory: a metric-tick for slice-4 must
// not re-render slice-7's card. Subscribers register a key path (top-level
// `NamlStore` field, or `slice:<id>` / `sprint:<id>` for nested rows) and
// only fire when that exact key's value changes by reference.

import { type NamlStore, emptyStore } from "./types.ts";

type AnyListener = (next: NamlStore) => void;
type KeyedListener = () => void;

/** Compute the listener-bucket key for a slice or sprint id. */
export function sliceKey(id: string): string {
  return `slice:${id}`;
}
export function sprintKey(id: string): string {
  return `sprint:${id}`;
}

export class Store {
  private state: NamlStore;
  private listeners = new Set<AnyListener>();
  private keyed = new Map<string, Set<KeyedListener>>();

  constructor(initial: NamlStore = emptyStore()) {
    this.state = initial;
  }

  getState(): NamlStore {
    return this.state;
  }

  /** Replace the whole state. Diff is computed by reference equality. */
  setState(next: NamlStore): void {
    if (next === this.state) return;
    const prev = this.state;
    this.state = next;
    this.fireDiff(prev, next);
  }

  /** Patch a top-level field and notify only its keyed subscribers. */
  patch<K extends keyof NamlStore>(key: K, value: NamlStore[K]): void {
    if (this.state[key] === value) return;
    this.state = { ...this.state, [key]: value };
    this.fireKey(key as string);
    for (const l of this.listeners) l(this.state);
  }

  /** Update a single slice or sprint row and notify only that row's subs. */
  patchSlice(id: string, value: NamlStore["slices"][string]): void {
    const slices = { ...this.state.slices, [id]: value };
    this.state = { ...this.state, slices };
    this.fireKey(sliceKey(id));
    this.fireKey("slices");
    for (const l of this.listeners) l(this.state);
  }

  patchSprint(id: string, value: NamlStore["sprints"][string]): void {
    const sprints = { ...this.state.sprints, [id]: value };
    this.state = { ...this.state, sprints };
    this.fireKey(sprintKey(id));
    this.fireKey("sprints");
    for (const l of this.listeners) l(this.state);
  }

  /** Subscribe to all state changes. Returns an unsubscribe fn. */
  subscribe(cb: AnyListener): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  /**
   * Subscribe to a specific keyed slice of the store (top-level field name
   * like `"sprints"` / `"project_metrics"` / `"syncStatus"`, OR `slice:<id>`,
   * OR `sprint:<id>`). Listener fires only when that bucket's reference
   * changes — this is the fine-grained re-render hook.
   */
  subscribeKey(key: string, cb: KeyedListener): () => void {
    let bucket = this.keyed.get(key);
    if (!bucket) {
      bucket = new Set();
      this.keyed.set(key, bucket);
    }
    bucket.add(cb);
    return () => {
      const b = this.keyed.get(key);
      if (!b) return;
      b.delete(cb);
      if (b.size === 0) this.keyed.delete(key);
    };
  }

  private fireKey(key: string): void {
    const bucket = this.keyed.get(key);
    if (!bucket) return;
    for (const cb of bucket) cb();
  }

  private fireDiff(prev: NamlStore, next: NamlStore): void {
    for (const k of Object.keys(next) as (keyof NamlStore)[]) {
      if (prev[k] !== next[k]) this.fireKey(k as string);
    }
    for (const l of this.listeners) l(next);
  }
}

/** Module-level singleton — convenient for the SPA. Tests instantiate fresh. */
export const store = new Store();
