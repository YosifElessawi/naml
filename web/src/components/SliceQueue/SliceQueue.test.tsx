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

  it("re-orders via FlipList — playFromSnapshot fires on commit", () => {
    const store = new Store();
    act(() => {
      store.patchSlice("slice-a", makeSlice("slice-a", "work"));
      store.patchSlice("slice-b", makeSlice("slice-b", "work"));
    });
    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = true; // jsdom-stable
    const playSpy = vi.spyOn(flip, "playFromSnapshot");

    render(<SliceQueue store={store} flip={flip} />);
    // First commit: play was called once with an empty prev map.
    expect(playSpy).toHaveBeenCalledTimes(1);
    expect(playSpy.mock.calls[0]?.[1]?.size).toBe(0);
    playSpy.mockClear();

    act(() => {
      // Promote slice-b to needs_human_review (priority 0) — slice-b
      // jumps to the front, FlipList animates from the prior layout.
      store.patchSlice("slice-b", makeSlice("slice-b", "needs_human_review"));
    });

    expect(playSpy).toHaveBeenCalled();
    // The prev map this time should be populated (2 slices).
    const lastCall = playSpy.mock.calls[playSpy.mock.calls.length - 1];
    expect(lastCall?.[1]?.size).toBe(2);
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
