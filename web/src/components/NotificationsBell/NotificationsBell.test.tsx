import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Store } from "../../store/store.ts";
import type { SliceSummary, SprintSummary } from "../../store/types.ts";
import { NotificationsBell, selectEscalations } from "./NotificationsBell.tsx";

function makeSprint(id: string, state: SprintSummary["state"], title = id): SprintSummary {
  return {
    id,
    title,
    state,
    slicesDone: 0,
    slicesTotal: 1,
    updatedAt: "2026-05-20T00:00:00Z",
  };
}

function makeSlice(id: string, state: SliceSummary["state"], title = id): SliceSummary {
  return {
    id,
    sprintId: "sprint-1",
    title,
    state,
    lane: 1,
    updatedAt: "2026-05-20T00:00:00Z",
  };
}

describe("NotificationsBell", () => {
  it("renders no-dot when there are no escalations", () => {
    const store = new Store();
    render(<NotificationsBell store={store} />);
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("data-has-escalations")).toBe("false");
    expect(btn.getAttribute("aria-label")).toMatch(/nothing needs attention/i);
  });

  it("flips to has-escalations and renders the count in the label", () => {
    const store = new Store();
    act(() => {
      store.patchSprint("sprint-1", makeSprint("sprint-1", "merge_blocked"));
      store.patchSlice("slice-3", makeSlice("slice-3", "needs_human_review"));
    });
    render(<NotificationsBell store={store} />);
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("data-has-escalations")).toBe("true");
    expect(btn.getAttribute("aria-label")).toMatch(/2 items? need attention/i);
  });

  it("ignores resting states like merged/work", () => {
    const store = new Store();
    act(() => {
      store.patchSlice("slice-1", makeSlice("slice-1", "merged"));
      store.patchSlice("slice-2", makeSlice("slice-2", "work"));
      store.patchSlice("slice-3", makeSlice("slice-3", "failed"));
    });
    render(<NotificationsBell store={store} />);
    expect(screen.getByRole("button").getAttribute("aria-label")).toMatch(/1 item/i);
  });

  it("popover lists each escalation by title and state", () => {
    const store = new Store();
    act(() => {
      store.patchSprint("sprint-1", makeSprint("sprint-1", "awaiting_signoff", "Cockpit"));
      store.patchSlice("slice-3", makeSlice("slice-3", "failed", "Dashboard"));
    });
    render(<NotificationsBell store={store} />);
    fireEvent.click(screen.getByRole("button"));
    const dialog = screen.getByRole("dialog");
    const items = within(dialog).getAllByRole("listitem");
    expect(items.length).toBe(2);
    const labels = items.map((i) => i.textContent ?? "");
    expect(labels.some((l) => l.includes("Cockpit"))).toBe(true);
    expect(labels.some((l) => l.includes("Dashboard"))).toBe(true);
  });

  it("popover renders an empty hint when nothing is escalated", () => {
    const store = new Store();
    render(<NotificationsBell store={store} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/no escalations/i)).toBeInTheDocument();
  });

  it("selectEscalations is the pure selector used by tests", () => {
    const store = new Store();
    act(() => {
      store.patchSprint("sprint-1", makeSprint("sprint-1", "failed"));
      store.patchSlice("slice-1", makeSlice("slice-1", "merged"));
      store.patchSlice("slice-2", makeSlice("slice-2", "needs_human_review"));
    });
    const items = selectEscalations(store.getState());
    expect(items.length).toBe(2);
    expect(items.map((i) => i.id).sort()).toEqual(["slice-2", "sprint-1"]);
  });
});
