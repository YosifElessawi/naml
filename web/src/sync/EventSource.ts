// SSE client that wires the server's `/events` stream into the cockpit
// store. Owns the 4-state sync derivation (LIVE / SLOW / LOST / CONNECTING)
// driven by time-since-last-event with 5s / 15s thresholds per Q8.
//
// Reconnect is left to the browser-native `EventSource` (exponential
// backoff with jitter). We layer a reconnect-attempt counter on top so
// the CONNECTING dot can show "attempt N/∞".

import type { Store } from "../store/store.ts";
import { recordTransition } from "../store/transitions.ts";
import type {
  ProjectMetrics,
  SliceMetrics,
  SliceState,
  SliceSummary,
  SprintMetrics,
  SprintState,
  SprintSummary,
  SyncStatus,
} from "../store/types.ts";

export const SLOW_THRESHOLD_MS = 5_000;
export const LOST_THRESHOLD_MS = 15_000;
const TICK_INTERVAL_MS = 1_000;

interface EventSourceLike {
  addEventListener(type: string, cb: (ev: MessageEvent) => void): void;
  close(): void;
  onerror: ((ev: Event) => void) | null;
  onopen: ((ev: Event) => void) | null;
}

type EventSourceFactory = (url: string) => EventSourceLike;

const defaultFactory: EventSourceFactory = (url) =>
  // The browser's EventSource matches the EventSourceLike contract above.
  new EventSource(url) as unknown as EventSourceLike;

export interface SnapshotPayload {
  sprints?: Record<string, Partial<SprintSummary> & { id: string }>;
  slices?: Record<string, Partial<SliceSummary> & { id: string }>;
  aggregates?: {
    per_project?: Partial<ProjectMetrics>;
    per_slice?: Record<string, Partial<SliceMetrics>>;
    per_sprint?: Record<string, Partial<SprintMetrics>>;
  };
}

export interface StateUpdatePayload {
  kind: "slice" | "sprint";
  id: string;
  delta: Record<string, unknown>;
}

export interface MetricTickPayload {
  slice?: string;
  sprint?: string;
  rollups?: {
    slice_cost?: number;
    slice_tokens_in?: number;
    slice_tokens_out?: number;
    slice_ctx_pct?: number;
    sprint_cost?: number;
    sprint_tokens?: number;
    project_today?: number;
    project_week?: number;
    project_30d?: number;
    project_lifetime?: number;
  };
}

export interface SseClientOptions {
  url?: string;
  factory?: EventSourceFactory;
  /** Monotonic clock — injected so tests can fast-forward. */
  now?: () => number;
  /** Schedule ticks for the sync-status derivation. */
  setInterval?: (cb: () => void, ms: number) => unknown;
  clearInterval?: (handle: unknown) => void;
}

export class SseClient {
  private es: EventSourceLike | null = null;
  private readonly url: string;
  private readonly factory: EventSourceFactory;
  private readonly now: () => number;
  private readonly setInterval: NonNullable<SseClientOptions["setInterval"]>;
  private readonly clearInterval: NonNullable<SseClientOptions["clearInterval"]>;
  private tickHandle: unknown = null;
  private lastEventMs = 0;

  constructor(
    private readonly store: Store,
    opts: SseClientOptions = {},
  ) {
    this.url = opts.url ?? "/events";
    this.factory = opts.factory ?? defaultFactory;
    this.now = opts.now ?? Date.now;
    this.setInterval = opts.setInterval ?? ((cb, ms) => globalThis.setInterval(cb, ms) as unknown);
    this.clearInterval =
      opts.clearInterval ??
      ((h) => globalThis.clearInterval(h as ReturnType<typeof globalThis.setInterval>));
  }

  start(): void {
    this.closeSocket();
    this.setSync("connecting");
    const es = this.factory(this.url);
    this.es = es;
    es.onopen = () => {
      this.lastEventMs = this.now();
      this.store.patch("reconnectAttempt", 0);
      this.setSync("live");
    };
    es.onerror = () => {
      // EventSource will auto-reconnect. We bump the attempt counter
      // so the sync dot shows "attempt N/∞" while it churns.
      const next = this.store.getState().reconnectAttempt + 1;
      this.store.patch("reconnectAttempt", next);
      this.deriveSync();
    };
    es.addEventListener("snapshot", (ev) =>
      this.handleSnapshot(parseJson<SnapshotPayload>(ev.data)),
    );
    es.addEventListener("state-update", (ev) =>
      this.handleStateUpdate(parseJson<StateUpdatePayload>(ev.data)),
    );
    es.addEventListener("metric-tick", (ev) =>
      this.handleMetricTick(parseJson<MetricTickPayload>(ev.data)),
    );
    es.addEventListener("ping", () => this.markEvent());

    this.tickHandle = this.setInterval(() => this.deriveSync(), TICK_INTERVAL_MS);
  }

  stop(): void {
    this.closeSocket();
    if (this.tickHandle !== null) {
      this.clearInterval(this.tickHandle);
      this.tickHandle = null;
    }
  }

  /** Force a sync-status recompute. Exposed for tests. */
  deriveSync(): SyncStatus {
    const last = this.lastEventMs;
    if (last === 0) {
      this.setSync("connecting");
      return "connecting";
    }
    const age = this.now() - last;
    let status: SyncStatus = "live";
    if (age >= LOST_THRESHOLD_MS) status = "lost";
    else if (age >= SLOW_THRESHOLD_MS) status = "slow";
    this.setSync(status);
    return status;
  }

  /* ─────────── handlers ─────────── */

  handleSnapshot(payload: SnapshotPayload | null): void {
    if (!payload) {
      this.markEvent();
      return;
    }
    const prev = this.store.getState();

    if (payload.sprints) {
      const sprints: Record<string, SprintSummary> = { ...prev.sprints };
      for (const [id, raw] of Object.entries(payload.sprints)) {
        sprints[id] = mergeSprint(sprints[id], raw, id);
      }
      this.store.patch("sprints", sprints);
    }
    if (payload.slices) {
      const slices: Record<string, SliceSummary> = { ...prev.slices };
      for (const [id, raw] of Object.entries(payload.slices)) {
        slices[id] = mergeSlice(slices[id], raw, id);
      }
      this.store.patch("slices", slices);
    }
    if (payload.aggregates?.per_project) {
      this.store.patch("project_metrics", {
        ...this.store.getState().project_metrics,
        ...payload.aggregates.per_project,
      });
    }
    if (payload.aggregates?.per_slice) {
      this.store.patch("slice_metrics", {
        ...this.store.getState().slice_metrics,
        ...mergeSliceMetricsBag(payload.aggregates.per_slice),
      });
    }
    if (payload.aggregates?.per_sprint) {
      this.store.patch("sprint_metrics", {
        ...this.store.getState().sprint_metrics,
        ...mergeSprintMetricsBag(payload.aggregates.per_sprint),
      });
    }
    this.markEvent();
  }

  handleStateUpdate(payload: StateUpdatePayload | null): void {
    if (!payload) {
      this.markEvent();
      return;
    }
    if (payload.kind === "slice") {
      const existing = this.store.getState().slices[payload.id];
      const merged = mergeSlice(existing, payload.delta, payload.id);
      this.store.patchSlice(payload.id, merged);
      if (
        existing &&
        typeof payload.delta.state === "string" &&
        existing.state !== payload.delta.state
      ) {
        recordTransition(this.store, {
          kind: "slice",
          targetId: payload.id,
          fromState: existing.state,
          toState: payload.delta.state as string,
          at: nowIso(),
        });
      } else if (!existing && typeof payload.delta.state === "string") {
        recordTransition(this.store, {
          kind: "slice",
          targetId: payload.id,
          fromState: null,
          toState: payload.delta.state as string,
          at: nowIso(),
        });
      }
    } else if (payload.kind === "sprint") {
      const existing = this.store.getState().sprints[payload.id];
      const merged = mergeSprint(existing, payload.delta, payload.id);
      this.store.patchSprint(payload.id, merged);
      if (
        existing &&
        typeof payload.delta.state === "string" &&
        existing.state !== payload.delta.state
      ) {
        recordTransition(this.store, {
          kind: "sprint",
          targetId: payload.id,
          fromState: existing.state,
          toState: payload.delta.state as string,
          at: nowIso(),
        });
      }
    }
    this.markEvent();
  }

  handleMetricTick(payload: MetricTickPayload | null): void {
    if (!payload?.rollups) {
      this.markEvent();
      return;
    }
    const r = payload.rollups;
    const prev = this.store.getState();

    if (
      r.project_today !== undefined ||
      r.project_week !== undefined ||
      r.project_30d !== undefined ||
      r.project_lifetime !== undefined
    ) {
      this.store.patch("project_metrics", {
        ...prev.project_metrics,
        today_cost: r.project_today ?? prev.project_metrics.today_cost,
        week_cost: r.project_week ?? prev.project_metrics.week_cost,
        last_30d_cost: r.project_30d ?? prev.project_metrics.last_30d_cost,
        lifetime_cost: r.project_lifetime ?? prev.project_metrics.lifetime_cost,
      });
    }

    if (payload.slice) {
      const existing = prev.slice_metrics[payload.slice] ?? {
        cost_usd: 0,
        tokens_in: 0,
        tokens_out: 0,
        cache_read: 0,
        cache_write: 0,
        ctx_pct: 0,
        last_event_at: null,
      };
      const updated: SliceMetrics = {
        ...existing,
        cost_usd: r.slice_cost ?? existing.cost_usd,
        tokens_in: r.slice_tokens_in ?? existing.tokens_in,
        tokens_out: r.slice_tokens_out ?? existing.tokens_out,
        ctx_pct: r.slice_ctx_pct ?? existing.ctx_pct,
        last_event_at: nowIso(),
      };
      this.store.patch("slice_metrics", {
        ...prev.slice_metrics,
        [payload.slice]: updated,
      });
    }

    if (payload.sprint) {
      const existing = prev.sprint_metrics[payload.sprint] ?? {
        cost_usd: 0,
        tokens: 0,
        tier1_hit_count: 0,
        lgtm_first_pass_count: 0,
      };
      const updated: SprintMetrics = {
        ...existing,
        cost_usd: r.sprint_cost ?? existing.cost_usd,
        tokens: r.sprint_tokens ?? existing.tokens,
      };
      this.store.patch("sprint_metrics", {
        ...prev.sprint_metrics,
        [payload.sprint]: updated,
      });
    }

    this.markEvent();
  }

  /* ─────────── private ─────────── */

  private setSync(status: SyncStatus): void {
    if (this.store.getState().syncStatus !== status) {
      this.store.patch("syncStatus", status);
    }
  }

  private markEvent(): void {
    this.lastEventMs = this.now();
    this.store.patch("lastEventAt", new Date(this.lastEventMs).toISOString());
    this.setSync("live");
  }

  private closeSocket(): void {
    if (this.es) {
      try {
        this.es.close();
      } catch {
        // best-effort
      }
      this.es = null;
    }
  }
}

function parseJson<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function nowIso(): string {
  return new Date().toISOString();
}

function mergeSlice(
  prev: SliceSummary | undefined,
  raw: Record<string, unknown>,
  id: string,
): SliceSummary {
  const base: SliceSummary = prev ?? {
    id,
    sprintId: typeof raw.sprintId === "string" ? (raw.sprintId as string) : "",
    title: "",
    state: "pending",
    lane: null,
    updatedAt: nowIso(),
  };
  return {
    ...base,
    id,
    sprintId: typeof raw.sprintId === "string" ? (raw.sprintId as string) : base.sprintId,
    title: typeof raw.title === "string" ? (raw.title as string) : base.title,
    state: typeof raw.state === "string" ? (raw.state as SliceState) : base.state,
    lane: typeof raw.lane === "number" ? (raw.lane as number) : base.lane,
    updatedAt: nowIso(),
  };
}

function mergeSprint(
  prev: SprintSummary | undefined,
  raw: Record<string, unknown>,
  id: string,
): SprintSummary {
  const base: SprintSummary = prev ?? {
    id,
    title: "",
    state: "planning",
    slicesDone: 0,
    slicesTotal: 0,
    updatedAt: nowIso(),
  };
  return {
    ...base,
    id,
    title: typeof raw.title === "string" ? (raw.title as string) : base.title,
    state: typeof raw.state === "string" ? (raw.state as SprintState) : base.state,
    slicesDone: typeof raw.slicesDone === "number" ? (raw.slicesDone as number) : base.slicesDone,
    slicesTotal:
      typeof raw.slicesTotal === "number" ? (raw.slicesTotal as number) : base.slicesTotal,
    updatedAt: nowIso(),
  };
}

function mergeSliceMetricsBag(
  raw: Record<string, Partial<SliceMetrics>>,
): Record<string, SliceMetrics> {
  const out: Record<string, SliceMetrics> = {};
  for (const [id, m] of Object.entries(raw)) {
    out[id] = {
      cost_usd: m.cost_usd ?? 0,
      tokens_in: m.tokens_in ?? 0,
      tokens_out: m.tokens_out ?? 0,
      cache_read: m.cache_read ?? 0,
      cache_write: m.cache_write ?? 0,
      ctx_pct: m.ctx_pct ?? 0,
      last_event_at: m.last_event_at ?? null,
    };
  }
  return out;
}

function mergeSprintMetricsBag(
  raw: Record<string, Partial<SprintMetrics>>,
): Record<string, SprintMetrics> {
  const out: Record<string, SprintMetrics> = {};
  for (const [id, m] of Object.entries(raw)) {
    out[id] = {
      cost_usd: m.cost_usd ?? 0,
      tokens: m.tokens ?? 0,
      tier1_hit_count: m.tier1_hit_count ?? 0,
      lgtm_first_pass_count: m.lgtm_first_pass_count ?? 0,
    };
  }
  return out;
}
