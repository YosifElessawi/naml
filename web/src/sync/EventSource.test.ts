import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Store } from "../store/store.ts";
import { LOST_THRESHOLD_MS, SLOW_THRESHOLD_MS, SseClient } from "./EventSource.ts";

interface FakeES {
  listeners: Map<string, (ev: MessageEvent) => void>;
  emit(event: string, data: unknown): void;
  close: () => void;
  closed: boolean;
  onerror: ((ev: Event) => void) | null;
  onopen: ((ev: Event) => void) | null;
}

function makeFake(): FakeES {
  const listeners = new Map<string, (ev: MessageEvent) => void>();
  const es: FakeES = {
    listeners,
    closed: false,
    onerror: null,
    onopen: null,
    emit(event, data) {
      const cb = listeners.get(event);
      if (!cb) return;
      cb({ data: JSON.stringify(data) } as MessageEvent);
    },
    close() {
      this.closed = true;
    },
  };
  return es;
}

describe("SseClient", () => {
  let store: Store;
  let fakeEs: FakeES;
  let nowMs = 1_000_000;
  let client: SseClient;
  const tickHandles: Array<() => void> = [];

  beforeEach(() => {
    store = new Store();
    fakeEs = makeFake();
    nowMs = 1_000_000;
    client = new SseClient(store, {
      url: "/events",
      factory: () => ({
        addEventListener: (type, cb) => fakeEs.listeners.set(type, cb),
        close: () => fakeEs.close(),
        get onerror() {
          return fakeEs.onerror;
        },
        set onerror(v) {
          fakeEs.onerror = v;
        },
        get onopen() {
          return fakeEs.onopen;
        },
        set onopen(v) {
          fakeEs.onopen = v;
        },
      }),
      now: () => nowMs,
      setInterval: (cb) => {
        tickHandles.push(cb);
        return tickHandles.length;
      },
      clearInterval: () => {},
    });
    client.start();
  });

  afterEach(() => {
    client.stop();
    tickHandles.length = 0;
  });

  it("starts in `connecting` and goes to `live` on snapshot", () => {
    expect(store.getState().syncStatus).toBe("connecting");
    fakeEs.emit("snapshot", { sprints: {}, slices: {} });
    expect(store.getState().syncStatus).toBe("live");
    expect(store.getState().lastEventAt).toBeTruthy();
  });

  it("merges snapshot sprints + slices + aggregates into the store", () => {
    fakeEs.emit("snapshot", {
      sprints: {
        "sprint-1": {
          id: "sprint-1",
          title: "Cockpit",
          state: "executing",
          slicesDone: 1,
          slicesTotal: 3,
        },
      },
      slices: {
        "slice-3": {
          id: "slice-3",
          sprintId: "sprint-1",
          title: "Dashboard",
          state: "work",
          lane: 1,
        },
      },
      aggregates: {
        per_project: { today_cost: 4.21, lifetime_cost: 342.16 },
        per_slice: { "slice-3": { cost_usd: 0.41, ctx_pct: 73 } },
        per_sprint: { "sprint-1": { cost_usd: 2.4, tokens: 1_000_000 } },
      },
    });
    const state = store.getState();
    expect(state.sprints["sprint-1"]?.state).toBe("executing");
    expect(state.slices["slice-3"]?.title).toBe("Dashboard");
    expect(state.project_metrics.today_cost).toBe(4.21);
    expect(state.project_metrics.lifetime_cost).toBe(342.16);
    expect(state.slice_metrics["slice-3"]?.ctx_pct).toBe(73);
    expect(state.sprint_metrics["sprint-1"]?.tokens).toBe(1_000_000);
  });

  it("state-update produces a transition record when the state changes", () => {
    fakeEs.emit("snapshot", {
      slices: {
        "slice-4": {
          id: "slice-4",
          sprintId: "sprint-1",
          title: "Stepper",
          state: "work",
          lane: 2,
        },
      },
    });
    fakeEs.emit("state-update", {
      kind: "slice",
      id: "slice-4",
      delta: { state: "review" },
    });
    const state = store.getState();
    expect(state.slices["slice-4"]?.state).toBe("review");
    expect(state.transitions[0]).toMatchObject({
      kind: "slice",
      targetId: "slice-4",
      fromState: "work",
      toState: "review",
    });
  });

  it("metric-tick updates project + slice + sprint rollups in place", () => {
    fakeEs.emit("metric-tick", {
      slice: "slice-4",
      sprint: "sprint-1",
      rollups: {
        slice_cost: 0.43,
        slice_tokens_in: 82140,
        slice_ctx_pct: 75,
        sprint_cost: 2.41,
        sprint_tokens: 2_100_000,
        project_today: 4.21,
        project_week: 24.8,
        project_30d: 118.42,
        project_lifetime: 342.16,
      },
    });
    const state = store.getState();
    expect(state.project_metrics.today_cost).toBe(4.21);
    expect(state.project_metrics.lifetime_cost).toBe(342.16);
    expect(state.slice_metrics["slice-4"]?.cost_usd).toBe(0.43);
    expect(state.sprint_metrics["sprint-1"]?.tokens).toBe(2_100_000);
  });

  it("derives `slow` after 5s and `lost` after 15s of silence", () => {
    fakeEs.emit("snapshot", {});
    expect(store.getState().syncStatus).toBe("live");
    nowMs += SLOW_THRESHOLD_MS + 100;
    client.deriveSync();
    expect(store.getState().syncStatus).toBe("slow");
    nowMs += LOST_THRESHOLD_MS;
    client.deriveSync();
    expect(store.getState().syncStatus).toBe("lost");
  });

  it("bumps reconnect attempts on error", () => {
    expect(store.getState().reconnectAttempt).toBe(0);
    if (fakeEs.onerror) fakeEs.onerror(new Event("error"));
    if (fakeEs.onerror) fakeEs.onerror(new Event("error"));
    expect(store.getState().reconnectAttempt).toBe(2);
  });

  it("stop() closes the underlying socket", () => {
    client.stop();
    expect(fakeEs.closed).toBe(true);
  });

  it("translates the aiohttp server's snake_case snapshot schema", () => {
    // Pinned against an actual `/events` payload to keep the adapter
    // honest. The server emits the on-disk schema verbatim — snake_case
    // keys and an inline `slices: Record<id, state>` map per sprint —
    // so the adapter has to derive slicesDone/slicesTotal/title.
    fakeEs.emit("snapshot", {
      ts: "2026-05-20T15:58:09.837842+00:00",
      sprints: {
        "2026-05-19-cockpit-v2": {
          sprint_id: "2026-05-19-cockpit-v2",
          state: "merge_blocked",
          lanes_configured: 1,
          lanes_effective: 2,
          slices: {
            "slice-1": "merged",
            "slice-2": "review_passed",
            "slice-9": "merged",
            "slice-14": "merged",
          },
          transitions: [],
        },
      },
      slices: {
        "2026-05-19-cockpit-v2::slice-1": {
          slice_id: "slice-1",
          state: "merged",
          branch: "naml/2026-05-19-cockpit-v2/slice-1",
        },
      },
    });
    const sprint = store.getState().sprints["2026-05-19-cockpit-v2"];
    expect(sprint).toBeDefined();
    expect(sprint?.title).toBe("2026-05-19-cockpit-v2");
    expect(sprint?.state).toBe("merge_blocked");
    expect(sprint?.slicesTotal).toBe(4);
    expect(sprint?.slicesDone).toBe(3); // three "merged" entries
    const slice = store.getState().slices["2026-05-19-cockpit-v2::slice-1"];
    expect(slice?.sprintId).toBe("2026-05-19-cockpit-v2");
    expect(slice?.title).toBe("slice-1");
    expect(slice?.state).toBe("merged");
  });

  it("holds CONNECTING after `onopen` until the first event arrives", () => {
    // start() already called in beforeEach.
    expect(store.getState().syncStatus).toBe("connecting");
    if (fakeEs.onopen) fakeEs.onopen(new Event("open"));
    expect(store.getState().syncStatus).toBe("connecting");
    expect(store.getState().reconnectAttempt).toBe(0);
    // First real event flips us to live.
    fakeEs.emit("ping", { t: "2026-05-20T00:00:00Z" });
    expect(store.getState().syncStatus).toBe("live");
  });
});
