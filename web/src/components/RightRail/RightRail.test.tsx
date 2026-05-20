import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RightRail } from "./RightRail.tsx";

describe("RightRail", () => {
  it("renders the wired sections when expanded", () => {
    render(<RightRail collapsed={false} />);
    // Cost timeline is now rendered by slice-13's <CostTimeline />, which
    // emits its own "Cost Timeline" header.
    expect(screen.getByText(/cost timeline/i)).toBeInTheDocument();
    expect(screen.getByText("SESSION QUOTA")).toBeInTheDocument();
    expect(screen.getByText("WEEKLY QUOTA")).toBeInTheDocument();
    // The activity ticker subscribes to the live store; on a fresh store
    // there are no entries to render — the section header still exists.
    expect(screen.getByText("SYNC")).toBeInTheDocument();
  });

  it("collapses to an icon strip that still shows a sync dot", () => {
    render(<RightRail collapsed />);
    expect(screen.queryByText(/cost timeline/i)).not.toBeInTheDocument();
    // SyncDot exposes a role="status" via its <output> element.
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
