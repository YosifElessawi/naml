import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SliceCard } from "./SliceCard";
import {
  blockedSliceFixture,
  failedSliceFixture,
  heldSliceFixture,
  mergedSliceFixture,
  needsHumanReviewSliceFixture,
  reviewSliceFixture,
  sliceCardFixturesByState,
  workingSliceFixture,
} from "./fixtures";
import type { SliceCardData, SliceState } from "./types";

const ALL_STATES: SliceState[] = [
  "pending",
  "setup",
  "work",
  "pr",
  "review",
  "merged",
  "failed",
  "needs_human_review",
  "blocked_upstream",
  "abandoned",
  "held",
];

describe("SliceCard.full · Q5 Option A layout", () => {
  it("renders slice id, title, kind badge, lane badge, and state pill in row 1", () => {
    render(<SliceCard data={workingSliceFixture} />);
    expect(screen.getByText("slice-4")).toBeInTheDocument();
    expect(screen.getByText(workingSliceFixture.title)).toBeInTheDocument();
    expect(screen.getByText("AFK")).toBeInTheDocument();
    expect(screen.getByText("lane-2")).toBeInTheDocument();
    expect(screen.getByText("WORK")).toBeInTheDocument();
  });

  it("renders dual progress meters with duration + context labels", () => {
    render(<SliceCard data={workingSliceFixture} />);
    expect(screen.getByText(/DURATION · IN STATE/i)).toBeInTheDocument();
    expect(screen.getByText(/CONTEXT WINDOW/i)).toBeInTheDocument();
    expect(screen.getByText(/6m 12s/)).toBeInTheDocument();
  });

  it("renders cost, tokens, gate, session id, and action buttons", () => {
    const { container } = render(<SliceCard data={workingSliceFixture} />);
    expect(screen.getByText("$0.41")).toBeInTheDocument();
    expect(screen.getByText(/82k in/)).toBeInTheDocument();
    expect(screen.getByText("typecheck ▶")).toBeInTheDocument();
    expect(screen.getByText(/9b0c…ef13/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy session id/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open in terminal/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /view branch/i })).toBeInTheDocument();
    // session id + action buttons line up to the right of the stats strip
    const actions = container.querySelector(".naml-scard__actions");
    const grow = container.querySelector(".naml-scard__grow");
    expect(actions).not.toBeNull();
    expect(grow).not.toBeNull();
  });

  it("renders the traversal pill chain with retry chip", () => {
    render(<SliceCard data={workingSliceFixture} />);
    expect(screen.getByText("setup")).toBeInTheDocument();
    expect(screen.getByText("work")).toBeInTheDocument();
    expect(screen.getByText("pr")).toBeInTheDocument();
    expect(screen.getByText(/retry 1\/2/i)).toBeInTheDocument();
  });

  it("renders the state stripe via the modifier class", () => {
    const { container } = render(<SliceCard data={workingSliceFixture} />);
    expect(
      (container.firstChild as HTMLElement).classList.contains("naml-scard--work"),
    ).toBe(true);
  });
});

describe("SliceCard · context-window meter colour-flip", () => {
  function ctxFillClasses(pct: number): DOMTokenList {
    const data: SliceCardData = {
      ...workingSliceFixture,
      context: { pct, used: pct * 2000, cap: 200_000 },
    };
    const { container } = render(<SliceCard data={data} />);
    const fill = container.querySelector(".naml-scard__mbar-fill--ctx");
    if (!fill) throw new Error("ctx meter fill not rendered");
    return fill.classList;
  }

  it("uses ok (green) below 70%", () => {
    const cls = ctxFillClasses(50);
    expect(cls.contains("naml-scard__mbar-fill--ok")).toBe(true);
    expect(cls.contains("naml-scard__mbar-fill--warn")).toBe(false);
    expect(cls.contains("naml-scard__mbar-fill--danger")).toBe(false);
  });

  it("uses warn (amber) between 70% and 85%", () => {
    const cls = ctxFillClasses(73);
    expect(cls.contains("naml-scard__mbar-fill--warn")).toBe(true);
    expect(cls.contains("naml-scard__mbar-fill--danger")).toBe(false);
  });

  it("uses danger (red) at 85% and above", () => {
    const cls = ctxFillClasses(88);
    expect(cls.contains("naml-scard__mbar-fill--danger")).toBe(true);
    expect(cls.contains("naml-scard__mbar-fill--warn")).toBe(false);
  });
});

describe("SliceCard · retry counter visibility", () => {
  it("does not render when retry.n is 0", () => {
    const data: SliceCardData = {
      ...workingSliceFixture,
      retry: { n: 0, cap: 2 },
    };
    render(<SliceCard data={data} />);
    expect(screen.queryByText("RETRY")).not.toBeInTheDocument();
  });

  it("does not render when retry is null", () => {
    const data: SliceCardData = { ...workingSliceFixture, retry: null };
    render(<SliceCard data={data} />);
    expect(screen.queryByText("RETRY")).not.toBeInTheDocument();
  });

  it("renders when retry.n is greater than 0", () => {
    render(<SliceCard data={workingSliceFixture} />);
    expect(screen.getByText("RETRY")).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
  });
});

describe("SliceCard · 11 state variants", () => {
  for (const state of ALL_STATES) {
    it(`renders state=${state} with correct data-state attribute and modifier class`, () => {
      const data = sliceCardFixturesByState[state];
      const { container } = render(<SliceCard data={data} />);
      const node = container.firstChild as HTMLElement;
      expect(node).toHaveAttribute("data-state", state);
      const stateMod = state.replace(/_/g, "-");
      expect(node.classList.contains(`naml-scard--${stateMod}`)).toBe(true);
      const pill = container.querySelector(`.naml-scard__pill--${stateMod}`);
      expect(pill).not.toBeNull();
    });
  }

  it("renders the HELD banner with the held reason", () => {
    render(<SliceCard data={heldSliceFixture} />);
    expect(screen.getByText("HELD")).toBeInTheDocument();
    expect(screen.getByText(/paused by user/i)).toBeInTheDocument();
  });

  it("renders the blocked banner with depends + touches", () => {
    render(<SliceCard data={blockedSliceFixture} />);
    expect(screen.getByText("DEPENDS")).toBeInTheDocument();
    expect(screen.getByText(/slice-3, slice-4/)).toBeInTheDocument();
    expect(screen.getByText("TOUCHES")).toBeInTheDocument();
  });

  it("merged variant shows PR number with the merged marker", () => {
    render(<SliceCard data={mergedSliceFixture} />);
    expect(screen.getByText(/#141 merged ✓/)).toBeInTheDocument();
  });

  it("review variant shows the inline verdict detail", () => {
    render(<SliceCard data={reviewSliceFixture} />);
    expect(screen.getByText(/VERDICT WAITING/i)).toBeInTheDocument();
    expect(screen.getByText(/A2-fresh/i)).toBeInTheDocument();
  });

  it("failed variant shows the failing gate label", () => {
    render(<SliceCard data={failedSliceFixture} />);
    expect(screen.getByText(/pytest tests\/aggregator\//)).toBeInTheDocument();
  });

  it("needs_human_review variant uses the HITL pill", () => {
    const { container } = render(<SliceCard data={needsHumanReviewSliceFixture} />);
    const pill = container.querySelector(".naml-scard__pill--needs-human-review");
    expect(pill).not.toBeNull();
    expect(pill?.textContent).toMatch(/HITL/);
  });
});

describe("SliceCard · card-flip animation on state change", () => {
  it("adds the flip modifier when the state changes", () => {
    const { container, rerender } = render(<SliceCard data={workingSliceFixture} />);
    const node = container.firstChild as HTMLElement;
    expect(node.classList.contains("naml-scard--flip")).toBe(false);
    rerender(<SliceCard data={{ ...workingSliceFixture, state: "pr" }} />);
    expect(node.classList.contains("naml-scard--flip")).toBe(true);
  });
});

describe("SliceCard.compact · lane kanban variant", () => {
  it("renders only DUR + CTX meters", () => {
    render(<SliceCard data={workingSliceFixture} variant="compact" />);
    expect(screen.getByText("DUR")).toBeInTheDocument();
    expect(screen.getByText("CTX")).toBeInTheDocument();
    expect(screen.queryByText("DURATION · IN STATE")).not.toBeInTheDocument();
  });

  it("does not render kind/lane chips in row 1", () => {
    render(<SliceCard data={workingSliceFixture} variant="compact" />);
    expect(screen.queryByText("AFK")).not.toBeInTheDocument();
    expect(screen.queryByText("lane-2")).not.toBeInTheDocument();
  });

  it("hides the retry counter when n is 0", () => {
    const data: SliceCardData = { ...workingSliceFixture, retry: { n: 0, cap: 2 } };
    render(<SliceCard data={data} variant="compact" />);
    expect(screen.queryByText("RETRY")).not.toBeInTheDocument();
  });

  it("shows the retry counter when n > 0", () => {
    render(<SliceCard data={workingSliceFixture} variant="compact" />);
    expect(screen.getByText("RETRY")).toBeInTheDocument();
  });

  it("applies the compact + state-modifier classes", () => {
    const { container } = render(
      <SliceCard data={workingSliceFixture} variant="compact" />,
    );
    const node = container.firstChild as HTMLElement;
    expect(node.classList.contains("naml-scard--compact")).toBe(true);
    expect(node.classList.contains("naml-scard--work")).toBe(true);
    expect(node).toHaveAttribute("data-variant", "compact");
  });
});
