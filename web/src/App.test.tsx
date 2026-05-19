import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { App } from "./App.tsx";

beforeEach(() => {
  window.location.hash = "";
});

afterEach(() => {
  window.location.hash = "";
});

describe("App", () => {
  it("renders the cockpit shell by default", () => {
    render(<App />);
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByLabelText("Telemetry rail")).toBeInTheDocument();
  });

  it("renders the playground when the route is #/playground", () => {
    window.location.hash = "/playground";
    render(<App />);
    expect(screen.getByText(/component playground/i)).toBeInTheDocument();
  });
});
