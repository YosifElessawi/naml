import { act, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Flip } from "../../lib/flip.ts";
import { Store } from "../../store/store.ts";
import type { SliceState, SliceSummary } from "../../store/types.ts";
import { SliceQueue } from "./SliceQueue.tsx";

function makeSlice(id: string, state: SliceState): SliceSummary {
  return {
    id,
    sprintId: "sprint-1",
    title: id,
    state,
    lane: 1,
    updatedAt: "2026-05-20T00:00:00Z",
  };
}

describe("SliceQueue", () => {
  it("sorts slices by state priority then id", () => {
    const store = new Store();
    act(() => {
      store.patchSlice("slice-z", makeSlice("slice-z", "merged"));
      store.patchSlice("slice-a", makeSlice("slice-a", "work"));
      store.patchSlice("slice-c", makeSlice("slice-c", "failed"));
      store.patchSlice("slice-b", makeSlice("slice-b", "work"));
    });
    render(<SliceQueue store={store} flip={new Flip((el) => el.dataset.sliceId)} />);
    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    const ids = items.map((i) => i.getAttribute("data-slice-id"));
    expect(ids).toEqual(["slice-c", "slice-a", "slice-b", "slice-z"]);
  });

  it("uses the Flip utility around the DOM reorder", () => {
    const store = new Store();
    act(() => {
      store.patchSlice("slice-a", makeSlice("slice-a", "work"));
      store.patchSlice("slice-b", makeSlice("slice-b", "work"));
    });
    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = false;
    const captureSpy = vi.spyOn(flip, "capture");
    const playSpy = vi.spyOn(flip, "play");
    render(<SliceQueue store={store} flip={flip} />);

    captureSpy.mockClear();
    playSpy.mockClear();

    act(() => {
      // Promote slice-b to needs_human_review (priority 0) — slice-b
      // should jump to the front, triggering a Flip play.
      store.patchSlice("slice-b", makeSlice("slice-b", "needs_human_review"));
    });

    expect(captureSpy).toHaveBeenCalled();
    expect(playSpy).toHaveBeenCalled();
  });

  it("renders the state stripe + state pill animation classes", () => {
    const store = new Store();
    act(() => store.patchSlice("slice-a", makeSlice("slice-a", "review")));
    render(<SliceQueue store={store} flip={new Flip((el) => el.dataset.sliceId)} />);
    const item = screen.getByRole("listitem");
    expect(item.className).toContain("naml-anim-card-stripe");
    expect(item.getAttribute("data-state")).toBe("review");
    expect(within(item).getByText(/REVIEW/i)).toBeInTheDocument();
  });
});
