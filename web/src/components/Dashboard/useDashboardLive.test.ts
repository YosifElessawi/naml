import { describe, expect, it } from "vitest";

import { Store } from "../../store/store.ts";
import type { SliceSummary, SprintSummary } from "../../store/types.ts";
import { selectDashboardLive } from "./useDashboardLive.ts";

function mkSprint(over: Partial<SprintSummary>): SprintSummary {
  return {
    id: "s1",
    title: "Sprint",
    state: "executing",
    slicesDone: 0,
    slicesTotal: 0,
    updatedAt: "2026-05-20T00:00:00Z",
    ...over,
  };
}
function mkSlice(over: Partial<SliceSummary>): SliceSummary {
  return {
    id: "slice-1",
    sprintId: "s1",
    title: "Slice",
    state: "pending",
    lane: 0,
    updatedAt: "2026-05-20T00:00:00Z",
    ...over,
  };
}

describe("selectDashboardLive", () => {
  it("renders an empty-but-valid payload from a fresh store", () => {
    const store = new Store();
    const data = selectDashboardLive(store.getState());
    expect(data.project.name).toMatch(/naml/);
    expect(data.cost.today.dollars).toBe(0);
    expect(data.sprints.active).toEqual([]);
    expect(data.sprints.queued).toEqual([]);
    expect(data.sprints.recent).toEqual([]);
  });

  it("groups sprints into active / queued / recent by their state", () => {
    const store = new Store();
    store.patch("sprints", {
      a: mkSprint({ id: "a", title: "Active", state: "executing", slicesTotal: 3, slicesDone: 1 }),
      q: mkSprint({ id: "q", title: "Queued", state: "planning", slicesTotal: 2 }),
      r1: mkSprint({
        id: "2026-05-19-r1",
        title: "R1",
        state: "complete",
        slicesTotal: 5,
        slicesDone: 5,
      }),
      r2: mkSprint({
        id: "2026-05-20-r2",
        title: "R2",
        state: "merge_blocked",
        slicesTotal: 4,
        slicesDone: 2,
      }),
    });
    store.patch("slices", {
      "a::slice-1": mkSlice({ id: "a::slice-1", sprintId: "a", state: "merged" }),
      "a::slice-2": mkSlice({ id: "a::slice-2", sprintId: "a", state: "work" }),
      "a::slice-3": mkSlice({ id: "a::slice-3", sprintId: "a", state: "pending" }),
    });
    const data = selectDashboardLive(store.getState());

    expect(data.sprints.active.map((r) => r.id)).toEqual(["a"]);
    expect(data.sprints.queued.map((r) => r.id)).toEqual(["q"]);
    expect(new Set(data.sprints.recent.map((r) => r.id))).toEqual(
      new Set(["2026-05-19-r1", "2026-05-20-r2"]),
    );
    const active = data.sprints.active[0];
    expect(active?.metaTopRight).toBe("slices 1/3");
    expect(active?.slices).toEqual(["merged", "work", "idle"]);
    expect(active?.stateLabel).toBe("EXECUTING");
  });

  it("derives cost block + project state directly from store metrics", () => {
    const store = new Store();
    store.patch("project_metrics", {
      today_cost: 4.21,
      week_cost: 24.8,
      last_30d_cost: 118.42,
      lifetime_cost: 342.16,
      tokens_in: 0,
      tokens_out: 0,
      cache_read: 0,
      cache_write: 0,
    });
    store.patch("sprints", {
      a: mkSprint({ state: "executing" }),
    });
    const data = selectDashboardLive(store.getState());
    expect(data.cost.today.dollars).toBe(4.21);
    expect(data.cost.lifetime.dollars).toBe(342.16);
    expect(data.project.state).toBe("ACTIVE");
  });

  it("idle when no sprint is executing", () => {
    const store = new Store();
    store.patch("sprints", {
      done: mkSprint({ state: "complete" }),
    });
    const data = selectDashboardLive(store.getState());
    expect(data.project.state).toBe("IDLE");
  });
});
