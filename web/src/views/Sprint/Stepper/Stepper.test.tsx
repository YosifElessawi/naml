import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Stepper } from "./Stepper.tsx";
import { stepperFixture } from "./fixture.ts";
import type { StepperData } from "./types.ts";

function withOverrides(overrides: Partial<StepperData>): StepperData {
  return { ...stepperFixture, ...overrides };
}

describe("Stepper", () => {
  it("renders all six sprint state pills", () => {
    render(<Stepper />);
    const labels = [
      "Planning",
      "Publishing",
      "Executing",
      "Awaiting sign-off",
      "Merging",
      "Complete",
    ];
    for (const lbl of labels) {
      expect(screen.getByText(lbl)).toBeInTheDocument();
    }
  });

  it("marks the executing pill as current by default", () => {
    render(<Stepper />);
    const stepper = screen.getByRole("list", { name: /sprint state stepper/i });
    const items = within(stepper).getAllByRole("listitem");
    const current = items.filter((el) => el.getAttribute("data-status") === "current");
    expect(current).toHaveLength(1);
    expect(current[0]?.getAttribute("data-state")).toBe("executing");
  });

  it("renders the slice sub-rail with 8 cells inside the executing pill", () => {
    render(<Stepper />);
    const subrail = screen.getByLabelText(/slice progress sub-rail/i);
    const cells = subrail.querySelectorAll("[data-slice-state]");
    expect(cells.length).toBe(8);
    // Mixed states from fixture — at least one of each of merged/review/work/idle.
    const states = Array.from(cells).map((c) => c.getAttribute("data-slice-state"));
    expect(states).toContain("merged");
    expect(states).toContain("review");
    expect(states).toContain("work");
    expect(states).toContain("idle");
  });

  it("renders the aux strip metrics", () => {
    render(<Stepper />);
    const aux = screen.getByLabelText(/sprint metrics strip/i);
    expect(within(aux).getByText("21m 14s")).toBeInTheDocument();
    expect(within(aux).getByText("~14m")).toBeInTheDocument();
    expect(within(aux).getByText("$2.41")).toBeInTheDocument();
    expect(within(aux).getByText("3 / 3")).toBeInTheDocument();
    expect(within(aux).getByText("5 / 8")).toBeInTheDocument();
    expect(within(aux).getByText(/last tick 2s ago/i)).toBeInTheDocument();
  });

  it("moves the current pulse to awaiting_signoff when the sprint advances", () => {
    render(<Stepper data={withOverrides({ currentState: "awaiting_signoff" })} />);
    const stepper = screen.getByRole("list", { name: /sprint state stepper/i });
    const items = within(stepper).getAllByRole("listitem");
    const byState: Record<string, string | null> = {};
    for (const el of items) {
      const state = el.getAttribute("data-state");
      if (state) byState[state] = el.getAttribute("data-status");
    }
    expect(byState.executing).toBe("done");
    expect(byState.awaiting_signoff).toBe("current");
    expect(byState.merging).toBe("future");
  });

  it("marks every pill done when the sprint is complete", () => {
    render(<Stepper data={withOverrides({ currentState: "complete" })} />);
    const stepper = screen.getByRole("list", { name: /sprint state stepper/i });
    const items = within(stepper).getAllByRole("listitem");
    for (const el of items) {
      expect(el.getAttribute("data-status")).toBe("done");
    }
  });

  it("lights the partial_failure exit chip amber when that exit has been hit", () => {
    render(<Stepper data={withOverrides({ hitExits: ["partial_failure"] })} />);
    const partial = document.querySelector('[data-exit="partial_failure"]');
    expect(partial).not.toBeNull();
    expect(partial?.getAttribute("data-lit")).toBe("true");

    // Sibling exits remain muted.
    const failed = document.querySelector('[data-exit="failed"]');
    expect(failed?.getAttribute("data-lit")).toBe("false");
  });

  it("keeps all alternate-exit dots muted when no exit has been hit", () => {
    render(<Stepper />);
    const dots = document.querySelectorAll("[data-exit]");
    expect(dots.length).toBe(3);
    for (const d of Array.from(dots)) {
      expect(d.getAttribute("data-lit")).toBe("false");
    }
  });
});
