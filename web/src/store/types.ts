// Shared types for the cockpit store. These mirror the Python server's
// SSE payloads (see naml/server.py::_build_snapshot and the state-update
// shape in naml/server.py::_state_watcher_loop). The web side intentionally
// keeps the shapes loose: status files evolve and we render best-effort.

export type SyncStatus = "connected" | "slow" | "lost" | "connecting";

export interface SliceState {
  slice_id: string;
  state: string;
  session_id?: string;
  branch?: string;
  worktree?: string;
  pr_url?: string;
  attempts?: Record<string, number>;
  last_error?: string;
  review_verdict?: string;
  transitions?: Array<{ state: string; entered_at: string; detail?: string }>;
}

export interface SprintState {
  sprint_id: string;
  state: string;
  lanes_configured?: number;
  lanes_effective?: number;
  slices?: Record<string, string>;
  transitions?: Array<{ state: string; entered_at: string; detail?: string }>;
}

// Per-project aggregator snapshot — populated from slice-10's aggregator
// once it lands. Until then the snapshot ships an empty object and UI uses
// fixtures, per slice-11 acceptance.
export interface ProjectAggregates {
  // Free-form: the aggregator owns the shape (see slice-10 summary).
  // We treat it as a Record so the UI can render best-effort fields.
  [key: string]: unknown;
}

export interface SnapshotPayload {
  ts: string;
  sprints: Record<string, SprintState>;
  slices: Record<string, SliceState>;
  aggregates: ProjectAggregates;
}

export interface StateUpdatePayload {
  kind: "slice" | "sprint";
  id: string;
  delta: SliceState | SprintState;
}

export interface PingPayload {
  t: string;
}

export interface StoreState {
  sprints: Record<string, SprintState>;
  slices: Record<string, SliceState>;
  aggregates: ProjectAggregates;
  syncStatus: SyncStatus;
  lastEventAt: number | null; // monotonic millisecond timestamp
  lastEventId: number | null; // SSE id of the most recent event we processed
}

export const initialStoreState: StoreState = {
  sprints: {},
  slices: {},
  aggregates: {},
  syncStatus: "connecting",
  lastEventAt: null,
  lastEventId: null,
};
