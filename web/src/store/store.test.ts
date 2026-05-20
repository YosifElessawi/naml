import { describe, expect, it, vi } from "vitest";

import { Store, sliceKey, sprintKey } from "./store.ts";
import { type SliceSummary, emptyStore } from "./types.ts";

function makeSlice(id: string, state: SliceSummary["state"] = "work"): SliceSummary {
  return {
    id,
    sprintId: "sprint-1",
    title: `Slice ${id}`,
    state,
    lane: 1,
    updatedAt: "2026-05-20T00:00:00.000Z",
  };
}

describe("Store", () => {
  it("starts in `connecting` sync state with empty rollups", () => {
    const s = new Store();
    const state = s.getState();
    expect(state.syncStatus).toBe("connecting");
    expect(state.sprints).toEqual({});
    expect(state.project_metrics.lifetime_cost).toBe(0);
    expect(state.transitions).toEqual([]);
  });

  it("patch fires keyed subscribers only for the changed top-level field", () => {
    const s = new Store();
    const syncCb = vi.fn();
    const metricCb = vi.fn();
    s.subscribeKey("syncStatus", syncCb);
    s.subscribeKey("project_metrics", metricCb);

    s.patch("syncStatus", "live");
    expect(syncCb).toHaveBeenCalledTimes(1);
    expect(metricCb).not.toHaveBeenCalled();
  });

  it("patchSlice notifies the slice's keyed subscribers and the `slices` bag", () => {
    const s = new Store();
    const sliceCb = vi.fn();
    const bagCb = vi.fn();
    const otherCb = vi.fn();
    s.subscribeKey(sliceKey("a"), sliceCb);
    s.subscribeKey("slices", bagCb);
    s.subscribeKey(sliceKey("b"), otherCb);

    s.patchSlice("a", makeSlice("a"));
    expect(sliceCb).toHaveBeenCalledTimes(1);
    expect(bagCb).toHaveBeenCalledTimes(1);
    expect(otherCb).not.toHaveBeenCalled();
  });

  it("patchSprint notifies the right subscribers", () => {
    const s = new Store();
    const cb = vi.fn();
    s.subscribeKey(sprintKey("sprint-1"), cb);
    s.patchSprint("sprint-1", {
      id: "sprint-1",
      title: "Sprint 1",
      state: "executing",
      slicesDone: 1,
      slicesTotal: 3,
      updatedAt: "2026-05-20T00:00:00.000Z",
    });
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("setState diffs against current and notifies only changed keys", () => {
    const init = emptyStore();
    const s = new Store(init);
    const syncCb = vi.fn();
    const slicesCb = vi.fn();
    s.subscribeKey("syncStatus", syncCb);
    s.subscribeKey("slices", slicesCb);

    s.setState({ ...init, syncStatus: "live" });
    expect(syncCb).toHaveBeenCalledTimes(1);
    expect(slicesCb).toHaveBeenCalledTimes(0);
  });

  it("unsubscribe removes the listener", () => {
    const s = new Store();
    const cb = vi.fn();
    const off = s.subscribeKey("syncStatus", cb);
    off();
    s.patch("syncStatus", "live");
    expect(cb).not.toHaveBeenCalled();
  });

  it("is a no-op when patched with the same value", () => {
    const s = new Store();
    const cb = vi.fn();
    s.subscribeKey("syncStatus", cb);
    // initial value is `connecting`; re-patching is a no-op
    s.patch("syncStatus", "connecting");
    expect(cb).not.toHaveBeenCalled();
  });
});
