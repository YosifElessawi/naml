import { act, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Store } from "../store.ts";
import type { SliceState, SliceSummary, SprintSummary } from "../types.ts";
import {
  selectDashboardData,
  selectHealthData,
  selectLanesData,
  selectSliceCardData,
  selectSliceDrawerData,
  selectSprintData,
  useDashboardData,
  useSliceCardData,
} from "./index.ts";

function sprint(
  id: string,
  state: SprintSummary["state"],
  slicesDone = 1,
  slicesTotal = 3,
): SprintSummary {
  return {
    id,
    title: id,
    state,
    slicesDone,
    slicesTotal,
    updatedAt: "2026-05-20T00:00:00Z",
  };
}

function slice(
  id: string,
  sprintId: string,
  state: SliceState,
  lane: number | null = 1,
): SliceSummary {
  return {
    id,
    sprintId,
    title: id,
    state,
    lane,
    updatedAt: "2026-05-20T00:00:00Z",
  };
}

describe("selectDashboardData", () => {
  it("buckets sprints into active / queued / recent and exposes cost cluster", () => {
    const store = new Store();
    act(() => {
      store.patchSprint("s1", sprint("s1", "executing"));
      store.patchSprint("s2", sprint("s2", "planning"));
      store.patchSprint("s3", sprint("s3", "complete"));
      store.patchSlice("a", slice("a", "s1", "work"));
      store.patchSlice("b", slice("b", "s1", "merged"));
      store.patch("project_metrics", {
        ...store.getState().project_metrics,
        today_cost: 4.21,
        lifetime_cost: 342.16,
      });
    });
    const data = selectDashboardData(store.getState(), "naml");
    expect(data.project.name).toBe("naml");
    expect(data.active.map((r) => r.id)).toEqual(["s1"]);
    expect(data.queued.map((r) => r.id)).toEqual(["s2"]);
    expect(data.recent.map((r) => r.id)).toEqual(["s3"]);
    expect(data.cost.today).toBe(4.21);
    expect(data.cost.lifetime).toBe(342.16);
    // Mini state strip for s1 should include both slice states in id order.
    expect(data.active[0]?.miniStrip).toEqual(["work", "merged"]);
  });

  it("inbox count flows through from the store", () => {
    const store = new Store();
    act(() => {
      store.patch("feedback_inbox", {
        ...store.getState().feedback_inbox,
        unfiledCount: 5,
      });
    });
    expect(selectDashboardData(store.getState()).inbox.count).toBe(5);
  });
});

describe("useDashboardData", () => {
  it("re-renders subscribers when relevant store keys change", () => {
    const store = new Store();
    let renders = 0;
    function Probe() {
      const data = useDashboardData(store);
      renders += 1;
      return <output data-testid="probe">{data.cost.today}</output>;
    }
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("probe").textContent).toBe("0");
    const before = renders;
    act(() => {
      store.patch("project_metrics", {
        ...store.getState().project_metrics,
        today_cost: 7.77,
      });
    });
    expect(getByTestId("probe").textContent).toBe("7.77");
    expect(renders).toBeGreaterThan(before);
  });
});

describe("selectSprintData", () => {
  it("returns null when the sprint id is unknown", () => {
    expect(selectSprintData(new Store().getState(), "missing")).toBeNull();
  });

  it("populates aux stats from sprint metrics", () => {
    const store = new Store();
    act(() => {
      store.patchSprint("s1", sprint("s1", "executing", 2, 5));
      store.patch("sprint_metrics", {
        s1: { cost_usd: 1.23, tokens: 1000, tier1_hit_count: 1, lgtm_first_pass_count: 2 },
      });
      store.patch("project_metrics", {
        ...store.getState().project_metrics,
        lifetime_cost: 99,
      });
    });
    const data = selectSprintData(store.getState(), "s1");
    expect(data?.state).toBe("executing");
    expect(data?.aux.costUsd).toBe(1.23);
    expect(data?.aux.tier1HitCount).toBe(1);
    expect(data?.aux.lgtmCount).toBe(2);
    expect(data?.aux.projectTotalUsd).toBe(99);
    expect(data?.aux.slicesDone).toBe(2);
    expect(data?.aux.slicesTotal).toBe(5);
  });
});

describe("selectLanesData", () => {
  it("groups slices by lane and picks the most-progressed as current", () => {
    const store = new Store();
    act(() => {
      store.patchSlice("a", slice("a", "s1", "merged", 1));
      store.patchSlice("b", slice("b", "s1", "work", 1));
      store.patchSlice("c", slice("c", "s1", "pending", 2));
      store.patchSlice("d", slice("d", "s1", "pending", null));
      store.patch("slice_metrics", {
        b: {
          cost_usd: 0.1,
          tokens_in: 0,
          tokens_out: 0,
          cache_read: 0,
          cache_write: 0,
          ctx_pct: 73,
          last_event_at: null,
        },
      });
    });
    const data = selectLanesData(store.getState(), "s1");
    expect(data.lanes.length).toBe(2);
    const lane1 = data.lanes.find((l) => l.lane === 1);
    expect(lane1?.currentSliceId).toBe("b");
    expect(lane1?.status).toBe("work");
    expect(lane1?.ctxPct).toBe(73);
    expect(data.queue.map((s) => s.id)).toEqual(["d"]);
  });
});

describe("selectSliceCardData", () => {
  it("merges slice row + slice_metrics or returns null if missing", () => {
    const store = new Store();
    expect(selectSliceCardData(store.getState(), "ghost")).toBeNull();
    act(() => {
      store.patchSlice("a", slice("a", "s1", "review"));
      store.patch("slice_metrics", {
        a: {
          cost_usd: 0.42,
          tokens_in: 100,
          tokens_out: 50,
          cache_read: 0,
          cache_write: 0,
          ctx_pct: 88,
          last_event_at: "2026-05-20T00:00:00Z",
        },
      });
    });
    const data = selectSliceCardData(store.getState(), "a");
    expect(data?.state).toBe("review");
    expect(data?.costUsd).toBe(0.42);
    expect(data?.ctxPct).toBe(88);
  });
});

describe("useSliceCardData", () => {
  it("re-renders for slice-specific events but not for unrelated patches", () => {
    const store = new Store();
    act(() => store.patchSlice("a", slice("a", "s1", "work")));

    let renders = 0;
    function Probe() {
      const data = useSliceCardData("a", store);
      renders += 1;
      return <output>{data?.state}</output>;
    }
    render(<Probe />);
    const baseline = renders;

    // Unrelated patch — different slice → should NOT re-render Probe.
    act(() => store.patchSlice("b", slice("b", "s1", "work")));
    expect(renders).toBe(baseline);

    // Patch slice "a" → should re-render.
    act(() => store.patchSlice("a", slice("a", "s1", "review")));
    expect(renders).toBeGreaterThan(baseline);
  });
});

describe("selectSliceDrawerData", () => {
  it("includes transition history filtered by slice id", () => {
    const store = new Store();
    act(() => {
      store.patchSlice("a", slice("a", "s1", "review"));
      store.patch("transitions", [
        {
          id: "t1",
          kind: "slice",
          targetId: "a",
          fromState: "work",
          toState: "review",
          at: "2026-05-20T00:00:00Z",
        },
        {
          id: "t2",
          kind: "slice",
          targetId: "b",
          fromState: "work",
          toState: "review",
          at: "2026-05-20T00:00:01Z",
        },
        {
          id: "t3",
          kind: "sprint",
          targetId: "s1",
          fromState: "planning",
          toState: "executing",
          at: "2026-05-20T00:00:02Z",
        },
      ]);
    });
    const data = selectSliceDrawerData(store.getState(), "a");
    expect(data?.history.map((h) => h.id)).toEqual(["t1"]);
  });
});

describe("selectHealthData", () => {
  it("maps aggregate points to per-metric series", () => {
    const store = new Store();
    act(() => {
      store.patch("aggregates_history", {
        points: [
          {
            day: "2026-05-19",
            cost_usd: 1,
            tokens: 10,
            sprints_started: 0,
            sprints_completed: 1,
            tier1_hit_count: 2,
            slice_fails: 0,
          },
          {
            day: "2026-05-20",
            cost_usd: 2,
            tokens: 20,
            sprints_started: 1,
            sprints_completed: 0,
            tier1_hit_count: 1,
            slice_fails: 1,
          },
        ],
        lastFetchedAt: "2026-05-20T00:00:00Z",
      });
    });
    const data = selectHealthData(store.getState());
    expect(data.costSeries.map((p) => p.value)).toEqual([1, 2]);
    expect(data.tokensSeries.map((p) => p.value)).toEqual([10, 20]);
    expect(data.sliceFailSeries.map((p) => p.value)).toEqual([0, 1]);
    expect(data.lastFetchedAt).toBe("2026-05-20T00:00:00Z");
  });
});
