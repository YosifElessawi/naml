import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RightRail } from "./RightRail.tsx";

describe("RightRail", () => {
  it("renders the cost timeline + quota sections when expanded", () => {
    render(<RightRail collapsed={false} />);
    expect(screen.getByText("COST TIMELINE")).toBeInTheDocument();
    expect(screen.getByText("SESSION QUOTA")).toBeInTheDocument();
    expect(screen.getByText("WEEKLY QUOTA")).toBeInTheDocument();
    expect(screen.getByText("THIS PROJECT")).toBeInTheDocument();
    expect(screen.getAllByTestId("activity-row")).toHaveLength(3);
  });

  it("collapses to an icon strip that still shows a sync dot", () => {
    render(<RightRail collapsed syncState="live" />);
    expect(screen.queryByText("COST TIMELINE")).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: /sync live/i })).toBeInTheDocument();
  });

  it("reflects sync state on the collapsed dot", () => {
    render(<RightRail collapsed syncState="lost" />);
    expect(screen.getByRole("status", { name: /sync lost/i })).toBeInTheDocument();
  });
});
