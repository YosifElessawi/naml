import { act, render } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Flip } from "../../lib/flip.ts";
import { FlipList } from "./FlipList.tsx";

function Harness({ flip, initial }: { flip: Flip; initial: string[] }) {
  const [order, setOrder] = useState(initial);
  return (
    <div>
      <button type="button" aria-label="reverse" onClick={() => setOrder([...order].reverse())}>
        reverse
      </button>
      <FlipList flip={flip} ariaLabel="harness">
        {order.map((id) => (
          <li key={id} data-slice-id={id}>
            {id}
          </li>
        ))}
      </FlipList>
    </div>
  );
}

describe("FlipList", () => {
  it("captures rects on first commit but does not play (prev empty)", () => {
    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = true;
    const playSpy = vi.spyOn(flip, "playFromSnapshot");
    const snapshotSpy = vi.spyOn(flip, "snapshot");

    render(<Harness flip={flip} initial={["a", "b"]} />);

    // On the first commit, playFromSnapshot was called with an empty map
    // (no animation) and snapshot was taken for next time.
    expect(playSpy).toHaveBeenCalledTimes(1);
    expect(playSpy.mock.calls[0]?.[1]?.size).toBe(0);
    expect(snapshotSpy).toHaveBeenCalledTimes(1);
  });

  it("plays from the prior snapshot when children re-order", () => {
    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = true;
    const playSpy = vi.spyOn(flip, "playFromSnapshot");

    const { getByLabelText } = render(<Harness flip={flip} initial={["a", "b", "c"]} />);
    playSpy.mockClear();

    act(() => {
      getByLabelText("reverse").click();
    });

    expect(playSpy).toHaveBeenCalledTimes(1);
    // The prev map passed in must reflect the *previous* commit (3 keys).
    expect(playSpy.mock.calls[0]?.[1]?.size).toBe(3);
  });

  it("uses a custom dataset key when keyAttr is supplied", () => {
    const flip = new Flip((el) => el.dataset.sprintId);
    flip.reducedMotionOverride = true;
    const snapshotSpy = vi.spyOn(flip, "snapshot");

    render(
      <FlipList flip={flip} keyAttr="sprintId" ariaLabel="sprints">
        <li data-sprint-id="s1">s1</li>
        <li data-sprint-id="s2">s2</li>
      </FlipList>,
    );

    expect(snapshotSpy).toHaveBeenCalledTimes(1);
  });

  it("renders the requested element type", () => {
    const { container } = render(
      <FlipList as="div" ariaLabel="cards">
        <div data-slice-id="a">a</div>
      </FlipList>,
    );
    expect(container.querySelector("div[aria-label='cards']")).not.toBeNull();
  });
});
