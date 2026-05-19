import { describe, expect, it } from "vitest";
import {
  type MetricTickPayload,
  type MetricsSlice,
  applyMetricTick,
  emptyMetricsSlice,
  metricTickPatch,
  parseMetricTickPayload,
} from "./metrics";

function makePayload(overrides: Partial<MetricTickPayload> = {}): MetricTickPayload {
  return {
    slice: "slice-4",
    sprint: "sp1",
    delta: {
      t: "2026-05-20T14:00:00+00:00",
      slice: "slice-4",
      turn: 1,
      tokens_in: 100,
      tokens_out: 20,
      cache_read: 50_000,
      cache_write: 0,
      cost_usd: 0.41,
      ctx_pct: 73,
    },
    rollups: {
      slice_cost: 0.41,
      slice_tokens_in: 100,
      slice_tokens_out: 20,
      slice_cache_read: 50_000,
      slice_cache_write: 0,
      slice_ctx_pct: 73,
      sprint_cost: 0.41,
      sprint_tokens: 50_120,
      project_today: 2.41,
      project_today_tokens: 1_000_000,
      project_week: 24.8,
      project_week_tokens: 5_000_000,
      project_30d: 118.42,
      project_30d_tokens: 25_000_000,
      project_lifetime: 342.16,
      project_lifetime_tokens: 50_000_000,
    },
    ...overrides,
  };
}

describe("metricTickPatch", () => {
  it("creates a slice entry on first observation", () => {
    const next = metricTickPatch(emptyMetricsSlice(), makePayload());
    expect(next.slice_metrics["slice-4"]).toBeDefined();
    expect(next.slice_metrics["slice-4"]?.cost_usd).toBe(0.41);
    expect(next.slice_metrics["slice-4"]?.ctx_pct).toBe(73);
    expect(next.slice_metrics["slice-4"]?.turns).toBe(1);
  });

  it("increments turn count and overwrites server-authoritative rollups", () => {
    let state = metricTickPatch(emptyMetricsSlice(), makePayload());
    state = metricTickPatch(
      state,
      makePayload({
        rollups: { ...makePayload().rollups, slice_cost: 0.82, slice_ctx_pct: 75 },
      }),
    );
    expect(state.slice_metrics["slice-4"]?.cost_usd).toBe(0.82);
    expect(state.slice_metrics["slice-4"]?.ctx_pct).toBe(75);
    expect(state.slice_metrics["slice-4"]?.turns).toBe(2);
  });

  it("indexes sprint rollups by sprint id", () => {
    const next = metricTickPatch(emptyMetricsSlice(), makePayload());
    expect(next.sprint_metrics.sp1).toBeDefined();
    expect(next.sprint_metrics.sp1?.cost_usd).toBe(0.41);
  });

  it("updates project metrics from the rollups block", () => {
    const next = metricTickPatch(emptyMetricsSlice(), makePayload());
    expect(next.project_metrics.today_cost).toBe(2.41);
    expect(next.project_metrics.week_cost).toBe(24.8);
    expect(next.project_metrics.last_30d_cost).toBe(118.42);
    expect(next.project_metrics.lifetime_cost).toBe(342.16);
    expect(next.project_metrics.first_event_at).toBe("2026-05-20T14:00:00+00:00");
  });

  it("preserves other slice entries by reference (fine-grained subscriber friendly)", () => {
    const initial = emptyMetricsSlice();
    initial.slice_metrics["slice-7"] = {
      slice_id: "slice-7",
      sprint_id: "sp1",
      cost_usd: 0.1,
      tokens_in: 0,
      tokens_out: 0,
      cache_read: 0,
      cache_write: 0,
      ctx_pct: 5,
      turns: 1,
      last_event_at: "",
    };
    const next = metricTickPatch(initial, makePayload());
    expect(next.slice_metrics["slice-7"]).toBe(initial.slice_metrics["slice-7"]);
  });

  it("leaves sprint_metrics unchanged when sprint id is empty", () => {
    const next = metricTickPatch(emptyMetricsSlice(), makePayload({ sprint: "" }));
    expect(Object.keys(next.sprint_metrics).length).toBe(0);
  });

  it("accumulates sprint tokens locally from the delta", () => {
    let state = metricTickPatch(emptyMetricsSlice(), makePayload());
    state = metricTickPatch(state, makePayload());
    expect(state.sprint_metrics.sp1?.tokens_in).toBe(200);
    expect(state.sprint_metrics.sp1?.cache_read).toBe(100_000);
  });
});

describe("applyMetricTick", () => {
  it("calls setState with the patched slice", () => {
    let state: MetricsSlice = emptyMetricsSlice();
    const store = {
      getState: () => state,
      setState: (patch: Partial<MetricsSlice>) => {
        state = { ...state, ...patch };
      },
    };
    applyMetricTick(store, makePayload());
    expect(state.slice_metrics["slice-4"]?.cost_usd).toBe(0.41);
    expect(state.project_metrics.lifetime_cost).toBe(342.16);
  });
});

describe("parseMetricTickPayload", () => {
  it("accepts a well-formed payload", () => {
    const parsed = parseMetricTickPayload(makePayload());
    expect(parsed).not.toBeNull();
    expect(parsed?.slice).toBe("slice-4");
  });

  it("rejects non-objects", () => {
    expect(parseMetricTickPayload(null)).toBeNull();
    expect(parseMetricTickPayload("not a payload")).toBeNull();
    expect(parseMetricTickPayload(42)).toBeNull();
  });

  it("rejects payloads missing required fields", () => {
    const bad = makePayload();
    // @ts-expect-error — testing runtime guard
    bad.rollups = undefined;
    expect(parseMetricTickPayload(bad)).toBeNull();
  });

  it("rejects payloads with empty slice id", () => {
    expect(parseMetricTickPayload(makePayload({ slice: "" }))).toBeNull();
  });

  it("rejects rollups missing project_lifetime", () => {
    const payload = makePayload();
    // @ts-expect-error — testing runtime guard
    payload.rollups.project_lifetime = undefined;
    expect(parseMetricTickPayload(payload)).toBeNull();
  });
});
