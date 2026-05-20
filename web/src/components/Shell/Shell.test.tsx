import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Shell } from "./Shell.tsx";

beforeEach(() => {
  window.location.hash = "";
});

afterEach(() => {
  window.location.hash = "";
});

describe("Shell", () => {
  it("renders dashboard by default", () => {
    render(<Shell />);
    expect(screen.getByRole("region", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("switches the main pane when a header tab is clicked", () => {
    render(<Shell />);
    fireEvent.click(screen.getByRole("tab", { name: "Sprint" }));
    expect(screen.getByRole("region", { name: "Sprint" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
    expect(screen.getByRole("region", { name: "Settings" })).toBeInTheDocument();
  });

  it("toggles the right rail with Cmd+\\", () => {
    render(<Shell />);
    expect(screen.getByLabelText("Telemetry rail")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "\\", metaKey: true });
    expect(screen.getByLabelText("Telemetry rail (collapsed)")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "\\", metaKey: true });
    expect(screen.getByLabelText("Telemetry rail")).toBeInTheDocument();
  });
});
