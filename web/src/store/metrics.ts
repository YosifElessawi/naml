/**
 * Metric-tick store slice — types + reducer for the cockpit's live cost /
 * token / context-window telemetry.
 *
 * Extends the slice-11 store with three indexed records:
 *
 * - ``slice_metrics: Record<slice_id, SliceMetrics>``   — live per-slice
 *   cost / tokens / context-window %.
 * - ``sprint_metrics: Record<sprint_id, SprintMetrics>`` — Σ slice rollups.
 * - ``project_metrics: ProjectMetrics``                  — today / week /
 *   30d / lifetime totals for the cost-timeline block.
 *
 * The module is store-shape-agnostic: it exports a pure ``metricTickPatch``
 * reducer that takes the SSE payload + current snapshot and returns the new
 * snapshot. ``applyMetricTick(store, payload)`` is a thin wrapper that
 * mutates a duck-typed store with ``getState`` / ``setState`` (matches the
 * slice-11 pub/sub shape).
 *
 * Server side (naml/aggregator.py) is the source of truth for all rollup
 * math; the browser is a dumb apply. Don't sum cost in TS.
 */

// --- shapes shared with the server (naml/aggregator.py) ---------------

export interface SliceMetrics {
  slice_id: string;
  sprint_id: string;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  ctx_pct: number;
  turns: number;
  last_event_at: string;
}

export interface SprintMetrics {
  sprint_id: string;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  turns: number;
  last_event_at: string;
}

export interface ProjectMetrics {
  today_cost: number;
  today_tokens: number;
  week_cost: number;
  week_tokens: number;
  last_30d_cost: number;
  last_30d_tokens: number;
  lifetime_cost: number;
  lifetime_tokens: number;
  first_event_at: string;
  last_event_at: string;
}

/** The ``rollups`` block sent on every ``metric-tick`` SSE event. */
export interface MetricTickRollups {
  slice_cost: number;
  slice_tokens_in: number;
  slice_tokens_out: number;
  slice_cache_read: number;
  slice_cache_write: number;
  slice_ctx_pct: number;
  sprint_cost: number;
  sprint_tokens: number;
  project_today: number;
  project_today_tokens: number;
  project_week: number;
  project_week_tokens: number;
  project_30d: number;
  project_30d_tokens: number;
  project_lifetime: number;
  project_lifetime_tokens: number;
}

/** A single JSONL token-event delta — same shape as the server's tokens.jsonl. */
export interface MetricTickDelta {
  t?: string;
  slice?: string;
  session?: string;
  turn?: number;
  tokens_in?: number;
  tokens_out?: number;
  cache_read?: number;
  cache_write?: number;
  cost_usd?: number;
  ctx_pct?: number;
}

/** Full SSE ``metric-tick`` payload — emitted by naml/metric_tick.py. */
export interface MetricTickPayload {
  slice: string;
  sprint: string;
  delta: MetricTickDelta;
  rollups: MetricTickRollups;
}

/** The pieces of cockpit-store state this slice owns. */
export interface MetricsSlice {
  slice_metrics: Record<string, SliceMetrics>;
  sprint_metrics: Record<string, SprintMetrics>;
  project_metrics: ProjectMetrics;
}

// --- default state + reducer -----------------------------------------

export const emptyProjectMetrics = (): ProjectMetrics => ({
  today_cost: 0,
  today_tokens: 0,
  week_cost: 0,
  week_tokens: 0,
  last_30d_cost: 0,
  last_30d_tokens: 0,
  lifetime_cost: 0,
  lifetime_tokens: 0,
  first_event_at: "",
  last_event_at: "",
});

export const emptyMetricsSlice = (): MetricsSlice => ({
  slice_metrics: {},
  sprint_metrics: {},
  project_metrics: emptyProjectMetrics(),
});

/**
 * Pure reducer. Returns a brand-new ``MetricsSlice`` with the payload
 * applied. Existing slices outside the payload are passed through by
 * reference so subscribers keyed on a different slice id don't re-render.
 */
export function metricTickPatch(prev: MetricsSlice, payload: MetricTickPayload): MetricsSlice {
  const sliceId = payload.slice;
  const sprintId = payload.sprint;
  const r = payload.rollups;
  const delta = payload.delta ?? {};
  const ts = delta.t ?? "";

  const prevSlice = prev.slice_metrics[sliceId];
  const turns = (prevSlice?.turns ?? 0) + 1;
  const nextSlice: SliceMetrics = {
    slice_id: sliceId,
    sprint_id: sprintId || prevSlice?.sprint_id || "",
    cost_usd: r.slice_cost,
    tokens_in: r.slice_tokens_in,
    tokens_out: r.slice_tokens_out,
    cache_read: r.slice_cache_read,
    cache_write: r.slice_cache_write,
    ctx_pct: r.slice_ctx_pct,
    turns,
    last_event_at: ts || prevSlice?.last_event_at || "",
  };

  // Sprint metrics are server-authoritative; the browser overwrites with
  // the latest rollup rather than summing locally.
  const nextSprintMap = { ...prev.sprint_metrics };
  if (sprintId) {
    const prevSprint = prev.sprint_metrics[sprintId];
    const sprintTurns = (prevSprint?.turns ?? 0) + 1;
    // The ``sprint_tokens`` rollup is a total; we still keep per-shape token
    // counts in the store for the UI's tokens-in/out breakdown, summing
    // locally from the delta keeps that field honest without an extra wire
    // field.
    nextSprintMap[sprintId] = {
      sprint_id: sprintId,
      cost_usd: r.sprint_cost,
      tokens_in: (prevSprint?.tokens_in ?? 0) + (delta.tokens_in ?? 0),
      tokens_out: (prevSprint?.tokens_out ?? 0) + (delta.tokens_out ?? 0),
      cache_read: (prevSprint?.cache_read ?? 0) + (delta.cache_read ?? 0),
      cache_write: (prevSprint?.cache_write ?? 0) + (delta.cache_write ?? 0),
      turns: sprintTurns,
      last_event_at: ts || prevSprint?.last_event_at || "",
    };
  }

  const prevProject = prev.project_metrics;
  const nextProject: ProjectMetrics = {
    today_cost: r.project_today,
    today_tokens: r.project_today_tokens,
    week_cost: r.project_week,
    week_tokens: r.project_week_tokens,
    last_30d_cost: r.project_30d,
    last_30d_tokens: r.project_30d_tokens,
    lifetime_cost: r.project_lifetime,
    lifetime_tokens: r.project_lifetime_tokens,
    first_event_at: prevProject.first_event_at || ts,
    last_event_at: ts || prevProject.last_event_at,
  };

  return {
    slice_metrics: { ...prev.slice_metrics, [sliceId]: nextSlice },
    sprint_metrics: nextSprintMap,
    project_metrics: nextProject,
  };
}

// --- store integration ------------------------------------------------

/**
 * Minimal duck-typed shape of the slice-11 store. We only need read +
 * partial-write; ``setState`` should preserve any other keys it doesn't
 * recognise. Tests pass a small fake implementing exactly these methods.
 */
export interface MetricsStoreLike<TFullState extends MetricsSlice = MetricsSlice> {
  getState(): TFullState;
  setState(patch: Partial<TFullState>): void;
}

/**
 * Apply a ``metric-tick`` payload to a store. This is the function the
 * EventSource handler installs as its ``metric-tick`` listener.
 */
export function applyMetricTick<TFullState extends MetricsSlice>(
  store: MetricsStoreLike<TFullState>,
  payload: MetricTickPayload,
): void {
  const current = store.getState();
  const patch = metricTickPatch(current, payload);
  store.setState(patch as Partial<TFullState>);
}

/**
 * Strict-ish runtime guard for incoming SSE payloads. Returns ``null`` for
 * obviously malformed data so the EventSource handler can ignore + log
 * without crashing the store.
 */
export function parseMetricTickPayload(raw: unknown): MetricTickPayload | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Partial<MetricTickPayload> & { rollups?: unknown };
  if (typeof obj.slice !== "string" || !obj.slice) return null;
  if (typeof obj.sprint !== "string") return null;
  if (!obj.rollups || typeof obj.rollups !== "object") return null;
  // Required numerics on rollups — guard against partial schema evolution.
  const r = obj.rollups as Partial<MetricTickRollups>;
  if (typeof r.project_lifetime !== "number") return null;
  return obj as MetricTickPayload;
}
