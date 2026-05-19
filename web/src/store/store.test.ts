// Unit tests for the cockpit store. Exercises the patch + subscribe API
// without going through the SSE wrapper, so failures here can be diagnosed
// in isolation.

import { describe, expect, it } from "vitest";

import { createStore } from ".";

describe("store", () => {
  it("applies a snapshot, replacing sprints/slices/aggregates", () => {
    const store = createStore();
    store.applySnapshot(
      {
        ts: "now",
        sprints: { A: { sprint_id: "A", state: "executing" } },
        slices: { "A::s1": { slice_id: "s1", state: "work" } },
        aggregates: { foo: 1 },
      },
      10,
    );
    const s = store.getState();
    expect(s.sprints.A?.state).toBe("executing");
    expect(s.slices["A::s1"]?.state).toBe("work");
    expect(s.aggregates.foo).toBe(1);
    expect(s.lastEventId).toBe(10);
  });

  it("merges a state-update on top of existing state", () => {
    const store = createStore();
    store.applySnapshot(
      {
        ts: "now",
        sprints: {},
        slices: {
          "A::s1": { slice_id: "s1", state: "work" },
          "A::s2": { slice_id: "s2", state: "pending" },
        },
        aggregates: {},
      },
      1,
    );
    store.applyStateUpdate(
      {
        kind: "slice",
        id: "A::s1",
        delta: { slice_id: "s1", state: "pr" },
      },
      2,
    );
    const s = store.getState();
    // Both slices remain; only s1 changed.
    expect(s.slices["A::s1"]?.state).toBe("pr");
    expect(s.slices["A::s2"]?.state).toBe("pending");
  });

  it("subscribeKey only fires when that key actually changes", () => {
    const store = createStore();
    let count = 0;
    store.subscribeKey("syncStatus", () => {
      count++;
    });
    store.setSyncStatus("connected");
    store.setSyncStatus("connected"); // no-op
    store.setSyncStatus("slow");
    expect(count).toBe(2);
  });

  it("subscribe returns an unsubscribe function", () => {
    const store = createStore();
    let count = 0;
    const off = store.subscribe(() => {
      count++;
    });
    store.setSyncStatus("connected");
    off();
    store.setSyncStatus("slow");
    expect(count).toBe(1);
  });
});
