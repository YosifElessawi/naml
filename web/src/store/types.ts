// Store shape consumed by every cockpit view. Slice-11 owns the SSE event
// shapes; slice-12 owns the metric rollups. Slice-13 adds `transitions`
// (activity ticker), `feedbackInbox`, and `aggregatesHistory` — the bits
// the UI needs that aren't pushed by SSE.

export type SyncStatus = "live" | "slow" | "lost" | "connecting";

export type SliceState =
  | "pending"
  | "setup"
  | "work"
  | "pr"
  | "review"
  | "merged"
  | "failed"
  | "needs_human_review"
  | "blocked_upstream"
  | "abandoned"
  | "held";

export type SprintState =
  | "planning"
  | "publishing"
  | "executing"
  | "awaiting_signoff"
  | "merging"
  | "complete"
  | "failed"
  | "merge_blocked";

export interface SliceSummary {
  id: string;
  sprintId: string;
  title: string;
  state: SliceState;
  lane: number | null;
  updatedAt: string; // ISO8601
}

export interface SprintSummary {
  id: string;
  title: string;
  state: SprintState;
  slicesDone: number;
  slicesTotal: number;
  updatedAt: string; // ISO8601
}

export interface ProjectMetrics {
  today_cost: number;
  week_cost: number;
  last_30d_cost: number;
  lifetime_cost: number;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
}

export interface SliceMetrics {
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  ctx_pct: number;
  last_event_at: string | null;
}

export interface SprintMetrics {
  cost_usd: number;
  tokens: number;
  tier1_hit_count: number;
  lgtm_first_pass_count: number;
}

export interface Transition {
  /** Monotonic id so the activity ticker can de-dupe and FLIP-animate. */
  id: string;
  kind: "slice" | "sprint";
  targetId: string;
  fromState: string | null;
  toState: string;
  /** ISO8601 of when the transition was observed. */
  at: string;
}

export interface FeedbackBullet {
  /** Stable id for the bullet so subscribers can dedupe across polls. */
  id: string;
  text: string;
  source: string;
  addedAt: string | null; // ISO8601 if present in inbox.md, else null
}

export interface FeedbackInbox {
  unfiledCount: number;
  bullets: FeedbackBullet[];
  /** When the last poll succeeded. Drives the "Synced Ns ago" desaturate. */
  lastPolledAt: string | null;
}

export interface AggregatesHistoryPoint {
  /** UTC day (yyyy-mm-dd) the rollup covers. */
  day: string;
  cost_usd: number;
  tokens: number;
  sprints_started: number;
  sprints_completed: number;
  tier1_hit_count: number;
  slice_fails: number;
}

export interface AggregatesHistory {
  points: AggregatesHistoryPoint[];
  /** When the last fetch returned. */
  lastFetchedAt: string | null;
}

export interface NamlStore {
  sprints: Record<string, SprintSummary>;
  slices: Record<string, SliceSummary>;
  syncStatus: SyncStatus;
  /** Most-recent first; capped at TRANSITIONS_CAP. */
  transitions: Transition[];
  project_metrics: ProjectMetrics;
  slice_metrics: Record<string, SliceMetrics>;
  sprint_metrics: Record<string, SprintMetrics>;
  feedback_inbox: FeedbackInbox;
  aggregates_history: AggregatesHistory;
  /** When the last SSE event arrived. Drives sync status + "Ns ago" labels. */
  lastEventAt: string | null;
  /** Reconnect attempt counter (only meaningful while connecting/lost). */
  reconnectAttempt: number;
}

export const TRANSITIONS_CAP = 50;

export const EMPTY_PROJECT_METRICS: ProjectMetrics = {
  today_cost: 0,
  week_cost: 0,
  last_30d_cost: 0,
  lifetime_cost: 0,
  tokens_in: 0,
  tokens_out: 0,
  cache_read: 0,
  cache_write: 0,
};

export const EMPTY_INBOX: FeedbackInbox = {
  unfiledCount: 0,
  bullets: [],
  lastPolledAt: null,
};

export const EMPTY_HISTORY: AggregatesHistory = {
  points: [],
  lastFetchedAt: null,
};

export function emptyStore(): NamlStore {
  return {
    sprints: {},
    slices: {},
    syncStatus: "connecting",
    transitions: [],
    project_metrics: { ...EMPTY_PROJECT_METRICS },
    slice_metrics: {},
    sprint_metrics: {},
    feedback_inbox: { ...EMPTY_INBOX },
    aggregates_history: { ...EMPTY_HISTORY },
    lastEventAt: null,
    reconnectAttempt: 0,
  };
}

// --- Compatibility aliases for slice-11's store/index.ts ----------------
// slice-11's store/index.ts (on main) imports `StoreState` and
// `initialStoreState` from this module. slice-13 renamed those to
// `NamlStore` / `emptyStore()`. Aliases keep both call sites working
// without a synchronous rename — the plumbing PR can unify later.

export type StoreState = NamlStore;
export const initialStoreState: StoreState = emptyStore();
