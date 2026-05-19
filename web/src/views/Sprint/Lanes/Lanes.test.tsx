import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Lanes } from "./Lanes.tsx";
import { lanesFixture } from "./fixture.ts";
import type { LanesData } from "./types.ts";

describe("Lanes", () => {
  it("renders the gantt time axis with now and eta markers", () => {
    render(<Lanes />);
    expect(screen.getByText("14:02")).toBeInTheDocument();
    expect(screen.getByText("14:20")).toBeInTheDocument();
    const now = screen.getByText("now");
    expect(now).toHaveClass("naml-lanes__gtime-tick--now");
    const eta = screen.getByText("eta");
    expect(eta).toHaveClass("naml-lanes__gtime-tick--eta");
  });

  it("renders one swimlane per lane with a now-line spanning each", () => {
    const { container } = render(<Lanes />);
    const lanes = container.querySelectorAll(".naml-lanes__lane-row");
    expect(lanes).toHaveLength(3);
    expect(screen.getByTestId("now-line-lane-1")).toBeInTheDocument();
    expect(screen.getByTestId("now-line-lane-2")).toBeInTheDocument();
    expect(screen.getByTestId("now-line-lane-3")).toBeInTheDocument();
    expect(screen.getByTestId("now-line-lane-2").getAttribute("style")).toContain(
      "left: 68%",
    );
  });

  it("colors gantt blocks by kind (work / review / merged) and renders a ghost past now", () => {
    const { container } = render(<Lanes />);
    expect(container.querySelector(".naml-lanes__block--work")).toBeInTheDocument();
    expect(container.querySelector(".naml-lanes__block--review")).toBeInTheDocument();
    expect(container.querySelectorAll(".naml-lanes__block--merged").length).toBeGreaterThanOrEqual(
      2,
    );
    const ghosts = container.querySelectorAll(".naml-lanes__ghost");
    expect(ghosts.length).toBeGreaterThanOrEqual(2);
  });

  it("marks the idle lane track with the striped variant and shows a ready band", () => {
    const { container } = render(<Lanes />);
    const idleTrack = container.querySelector(".naml-lanes__track--idle");
    expect(idleTrack).not.toBeNull();
    expect(screen.getByTestId("ready-lane-3")).toBeInTheDocument();
  });

  it("renders three lane kanban cards with the right state pills and ctx warn", () => {
    const { container } = render(<Lanes />);
    const cards = container.querySelectorAll(".naml-lanes__card");
    expect(cards).toHaveLength(3);
    expect(container.querySelector(".naml-lanes__card--review")).toBeInTheDocument();
    expect(container.querySelector(".naml-lanes__card--work")).toBeInTheDocument();
    expect(container.querySelector(".naml-lanes__card--idle")).toBeInTheDocument();

    const workCard = container.querySelector(
      ".naml-lanes__card--work",
    ) as HTMLElement;
    expect(within(workCard).getByText("73%")).toHaveClass(
      "naml-lanes__meter-value--warn",
    );

    const idleCard = container.querySelector(
      ".naml-lanes__card--idle",
    ) as HTMLElement;
    expect(
      within(idleCard).getByText(/READY · NEXT SLICE WILL LAND HERE/),
    ).toBeInTheDocument();
  });

  it("renders queue chips with neutral blocked and green-tinged ready styling", () => {
    const { container } = render(<Lanes />);
    const chips = container.querySelectorAll(".naml-lanes__chip");
    expect(chips.length).toBe(4);
    const ready = container.querySelectorAll('[data-kind="ready"]');
    const blocked = container.querySelectorAll('[data-kind="blocked"]');
    expect(ready).toHaveLength(2);
    expect(blocked).toHaveLength(2);
    ready.forEach((el) => expect(el).toHaveClass("naml-lanes__chip--ready"));
    expect(screen.getByText("3 lanes · 2 ready · 2 blocked · 1 idle")).toBeInTheDocument();
  });

  it("accepts a custom data prop overriding the fixture", () => {
    const data: LanesData = {
      ...lanesFixture,
      queueSummary: "1 lane · 0 ready",
      queue: [],
      cards: [{ laneId: "solo", status: "idle", idleFor: "0s" }],
      tracks: [
        {
          laneId: "solo",
          status: "idle",
          blocks: [],
          ghosts: [],
        },
      ],
    };
    render(<Lanes data={data} />);
    expect(screen.getByText("1 lane · 0 ready")).toBeInTheDocument();
    // lane label appears in both the gantt row and the kanban card
    expect(screen.getAllByText("solo")).toHaveLength(2);
  });
});
