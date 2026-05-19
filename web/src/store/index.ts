// Tiny pub/sub store used by the cockpit. Intentionally hand-rolled to
// avoid a Redux/Zustand dependency for what is effectively a single
// stream of patches. The store mutates state immutably so consumers can
// shallow-compare on whichever key they subscribed to.
//
// Subscribers can either listen to the whole state (`subscribe(cb)`) or
// pin to a single key (`subscribeKey("syncStatus", cb)`) — the latter
// lets the header's sync dot re-render without re-rendering everything
// else when only `syncStatus` changes.

import type {
  PingPayload,
  SliceState,
  SnapshotPayload,
  SprintState,
  StateUpdatePayload,
  StoreState,
  SyncStatus,
} from "./types";
import { initialStoreState } from "./types";

type Listener = (state: StoreState) => void;
type KeyListener<K extends keyof StoreState> = (value: StoreState[K]) => void;
// Erased type used internally for storage; per-key callsites narrow back via
// the public `subscribeKey<K>` overload.
type AnyKeyListener = (value: StoreState[keyof StoreState]) => void;

export interface Store {
  getState(): StoreState;
  subscribe(fn: Listener): () => void;
  subscribeKey<K extends keyof StoreState>(key: K, fn: KeyListener<K>): () => void;
  // Direct setter — used by the EventSource wrapper to flip syncStatus.
  setSyncStatus(status: SyncStatus): void;
  // Apply an incoming SSE event to the store.
  applySnapshot(payload: SnapshotPayload, eventId: number | null): void;
  applyStateUpdate(payload: StateUpdatePayload, eventId: number | null): void;
  applyPing(payload: PingPayload, eventId: number | null): void;
  // Test-only: reset to defaults.
  _reset(): void;
}

export function createStore(initial: StoreState = initialStoreState): Store {
  let state: StoreState = { ...initial };
  const listeners = new Set<Listener>();
  const keyListeners = new Map<keyof StoreState, Set<AnyKeyListener>>();

  function notifyAll(prev: StoreState) {
    for (const fn of listeners) fn(state);
    for (const [key, set] of keyListeners) {
      if (prev[key] !== state[key]) {
        for (const fn of set) fn(state[key]);
      }
    }
  }

  function patch(next: Partial<StoreState>) {
    const prev = state;
    state = { ...state, ...next };
    notifyAll(prev);
  }

  function markEvent(eventId: number | null) {
    if (eventId !== null) {
      state = { ...state, lastEventId: eventId, lastEventAt: Date.now() };
    } else {
      state = { ...state, lastEventAt: Date.now() };
    }
  }

  return {
    getState() {
      return state;
    },

    subscribe(fn) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },

    subscribeKey<K extends keyof StoreState>(key: K, fn: KeyListener<K>) {
      let set = keyListeners.get(key);
      if (!set) {
        set = new Set();
        keyListeners.set(key, set);
      }
      const erased = fn as unknown as AnyKeyListener;
      set.add(erased);
      return () => {
        set?.delete(erased);
        if (set && set.size === 0) {
          keyListeners.delete(key);
        }
      };
    },

    setSyncStatus(status) {
      if (state.syncStatus === status) return;
      patch({ syncStatus: status });
    },

    applySnapshot(payload, eventId) {
      const prev = state;
      markEvent(eventId);
      state = {
        ...state,
        sprints: { ...payload.sprints },
        slices: { ...payload.slices },
        aggregates: { ...payload.aggregates },
      };
      notifyAll(prev);
    },

    applyStateUpdate(payload, eventId) {
      const prev = state;
      markEvent(eventId);
      if (payload.kind === "slice") {
        state = {
          ...state,
          slices: { ...state.slices, [payload.id]: payload.delta as SliceState },
        };
      } else {
        state = {
          ...state,
          sprints: { ...state.sprints, [payload.id]: payload.delta as SprintState },
        };
      }
      notifyAll(prev);
    },

    applyPing(_payload, eventId) {
      // Pings only touch the liveness fields — no other state changes.
      // We still re-notify so anything keyed off `lastEventAt` updates.
      const prev = state;
      markEvent(eventId);
      notifyAll(prev);
    },

    _reset() {
      const prev = state;
      state = { ...initialStoreState };
      notifyAll(prev);
    },
  };
}

// Module-level singleton — most consumers want the shared store, not
// an independent instance. Tests can call createStore() directly.
export const store: Store = createStore();

export type { StoreState, SyncStatus } from "./types";
