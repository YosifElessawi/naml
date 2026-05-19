// Browser-side wrapper around the native EventSource API.
//
// What this owns:
//   - Connect to /events on the same origin
//   - Apply incoming snapshot / state-update / ping events to the store
//   - Derive `syncStatus` (connecting | connected | slow | lost) from
//     time-since-last-event and surface that on the store. The header's
//     sync dot (slice-2) subscribes to it.
//   - Auto-reconnect comes "for free" with EventSource, but native impl
//     uses its own retry timer. We additionally guard against the page
//     having been backgrounded for hours: when document becomes visible
//     and the connection is in `lost`, we force a reconnect to skip the
//     browser's potentially-long retry backoff.
//
// The 5s / 15s thresholds come from Q8 of the grilling spec. Heartbeat
// fires server-side every 2s, so 5s without any event is conservative
// (two missed pings) and 15s is clearly dead.

import type { Store } from "../store";
import type { PingPayload, SnapshotPayload, StateUpdatePayload, SyncStatus } from "../store/types";

export const SLOW_AFTER_MS = 5_000;
export const LOST_AFTER_MS = 15_000;
const SYNC_TICK_MS = 1_000;

export interface NamlEventSourceOptions {
  url?: string;
  // Inject a constructor so tests can plug in a mock instead of relying
  // on jsdom's EventSource (which it does not implement out of the box).
  EventSourceCtor?: typeof EventSource;
  // Inject a clock for tests that don't want to rely on Date.now().
  now?: () => number;
  // Override the sync-status tick interval — handy in tests to verify
  // SLOW/LOST transitions without sleeping seconds.
  syncTickMs?: number;
}

export class NamlEventSource {
  private readonly store: Store;
  private readonly url: string;
  private readonly EventSourceCtor: typeof EventSource;
  private readonly now: () => number;
  private readonly syncTickMs: number;

  private es: EventSource | null = null;
  private tickHandle: ReturnType<typeof setInterval> | null = null;
  private visibilityHandler: (() => void) | null = null;
  private started = false;

  constructor(store: Store, opts: NamlEventSourceOptions = {}) {
    this.store = store;
    this.url = opts.url ?? "/events";
    this.EventSourceCtor = opts.EventSourceCtor ?? (globalThis.EventSource as typeof EventSource);
    this.now = opts.now ?? (() => Date.now());
    this.syncTickMs = opts.syncTickMs ?? SYNC_TICK_MS;
  }

  /** Open the SSE connection and start the sync-status ticker. */
  start(): void {
    if (this.started) return;
    if (!this.EventSourceCtor) {
      // Browser has no EventSource. Park syncStatus as lost so the UI is
      // honest about the situation rather than appearing live.
      this.store.setSyncStatus("lost");
      return;
    }
    this.started = true;
    this.store.setSyncStatus("connecting");
    this.connect();
    this.tickHandle = setInterval(() => this.tickSyncStatus(), this.syncTickMs);
    if (typeof document !== "undefined") {
      this.visibilityHandler = () => {
        if (document.visibilityState === "visible" && this.store.getState().syncStatus === "lost") {
          this.forceReconnect();
        }
      };
      document.addEventListener("visibilitychange", this.visibilityHandler);
    }
  }

  /** Close the SSE connection and stop background work. */
  stop(): void {
    this.started = false;
    if (this.es) {
      this.es.close();
      this.es = null;
    }
    if (this.tickHandle !== null) {
      clearInterval(this.tickHandle);
      this.tickHandle = null;
    }
    if (this.visibilityHandler && typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this.visibilityHandler);
      this.visibilityHandler = null;
    }
    this.store.setSyncStatus("lost");
  }

  /** Tear down the current connection and immediately reopen. */
  forceReconnect(): void {
    if (!this.started) return;
    if (this.es) {
      this.es.close();
      this.es = null;
    }
    this.store.setSyncStatus("connecting");
    this.connect();
  }

  // --- internals ---------------------------------------------------------

  private connect(): void {
    const es = new this.EventSourceCtor(this.url);
    this.es = es;
    es.addEventListener("open", () => {
      this.store.setSyncStatus("connecting");
    });
    es.addEventListener("error", () => {
      // EventSource will retry on its own; reflect the in-between state.
      const elapsed = this.timeSinceLastEvent();
      if (elapsed === null || elapsed >= LOST_AFTER_MS) {
        this.store.setSyncStatus("lost");
      } else {
        this.store.setSyncStatus("connecting");
      }
    });
    es.addEventListener("snapshot", (ev) => {
      this.handle<SnapshotPayload>(ev as MessageEvent, (data, id) => {
        this.store.applySnapshot(data, id);
        this.store.setSyncStatus("connected");
      });
    });
    es.addEventListener("state-update", (ev) => {
      this.handle<StateUpdatePayload>(ev as MessageEvent, (data, id) => {
        this.store.applyStateUpdate(data, id);
        this.store.setSyncStatus("connected");
      });
    });
    es.addEventListener("ping", (ev) => {
      this.handle<PingPayload>(ev as MessageEvent, (data, id) => {
        this.store.applyPing(data, id);
        this.store.setSyncStatus("connected");
      });
    });
  }

  private handle<T>(ev: MessageEvent, fn: (data: T, eventId: number | null) => void): void {
    let data: T;
    try {
      data = JSON.parse(ev.data) as T;
    } catch {
      // A malformed payload should not break the stream — log and skip.
      // eslint-disable-next-line no-console
      console.warn("naml: dropped malformed SSE payload", ev.type);
      return;
    }
    const id = ev.lastEventId ? Number.parseInt(ev.lastEventId, 10) : null;
    fn(data, Number.isFinite(id as number) ? id : null);
  }

  private tickSyncStatus(): void {
    const elapsed = this.timeSinceLastEvent();
    if (elapsed === null) return;
    let next: SyncStatus = "connected";
    if (elapsed >= LOST_AFTER_MS) next = "lost";
    else if (elapsed >= SLOW_AFTER_MS) next = "slow";
    // Don't downgrade out of `connecting` until we actually see an event;
    // `applyPing` / `applySnapshot` / `applyStateUpdate` flip to connected.
    if (this.store.getState().syncStatus === "connecting") return;
    this.store.setSyncStatus(next);
  }

  private timeSinceLastEvent(): number | null {
    const lastAt = this.store.getState().lastEventAt;
    if (lastAt === null) return null;
    return this.now() - lastAt;
  }
}

/** Convenience: create + start in one call. */
export function startEventSource(store: Store, opts: NamlEventSourceOptions = {}): NamlEventSource {
  const es = new NamlEventSource(store, opts);
  es.start();
  return es;
}
