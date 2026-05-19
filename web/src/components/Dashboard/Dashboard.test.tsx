import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import dashboardJson from "../../fixtures/dashboard.json";
import { Dashboard } from "./Dashboard";
import type { DashboardData } from "./types";

const data = dashboardJson as DashboardData;

describe("Dashboard", () => {
  beforeEach(() => {
    window.location.hash = "";
  });
  afterEach(() => {
    window.location.hash = "";
  });

  it("renders the hero with project name and state pill", () => {
    render(<Dashboard />);
    expect(screen.getByText(data.project.name)).toBeInTheDocument();
    expect(screen.getByLabelText(/project state/i)).toHaveTextContent(data.project.state);
    expect(screen.getByText(/sprints shipped/i)).toBeInTheDocument();
  });

  it("renders the Q7b primary cost cluster (4 stats)", () => {
    render(<Dashboard />);
    const costCluster = screen.getByLabelText(/cost timeline/i);
    const cluster = within(costCluster);
    expect(cluster.getByText("TODAY")).toBeInTheDocument();
    expect(cluster.getByText("THIS WEEK")).toBeInTheDocument();
    expect(cluster.getByText("LAST 30 DAYS")).toBeInTheDocument();
    expect(cluster.getByText("LIFETIME · PROJECT")).toBeInTheDocument();
    expect(cluster.getByText(`$${data.cost.today.dollars.toFixed(2)}`)).toBeInTheDocument();
    expect(cluster.getByText(`$${data.cost.lifetime.dollars.toFixed(2)}`)).toBeInTheDocument();
  });

  it("renders the secondary health row with tone classes", () => {
    render(<Dashboard />);
    const healthRow = screen.getByLabelText(/^pipeline health$/i);
    const row = within(healthRow);
    for (const stat of data.health) {
      expect(row.getByText(stat.label)).toBeInTheDocument();
      expect(row.getByText(stat.value)).toBeInTheDocument();
    }
  });

  it("groups sprints into ACTIVE / QUEUED / RECENT with counts", () => {
    render(<Dashboard />);
    const headings = screen.getAllByRole("heading", { level: 2 });
    const headingText = headings
      .map((h) => h.textContent ?? "")
      .map((t) => t.replace(/\s+/g, " ").trim());
    expect(headingText).toEqual(
      expect.arrayContaining([
        `ACTIVE ${data.sprints.active.length}`,
        `QUEUED ${data.sprints.queued.length}`,
        `RECENT last ${data.sprints.recent.length}`,
      ]),
    );
  });

  it("renders each sprint row with a state-coloured mini-strip cell per slice", () => {
    render(<Dashboard />);
    const active = data.sprints.active[0];
    expect(active).toBeDefined();
    if (!active) return;
    const row = screen.getByRole("button", { name: new RegExp(`Open sprint ${active.title}`, "i") });
    const cells = row.querySelectorAll("[data-slice-state]");
    expect(cells.length).toBe(active.slices.length);
    active.slices.forEach((sliceColor, i) => {
      expect(cells[i]?.getAttribute("data-slice-state")).toBe(sliceColor);
    });
  });

  it("navigates to /sprint/<id> on row click", () => {
    render(<Dashboard />);
    const queued = data.sprints.queued[0];
    expect(queued).toBeDefined();
    if (!queued) return;
    const row = screen.getByRole("button", { name: new RegExp(`Open sprint ${queued.title}`, "i") });
    fireEvent.click(row);
    expect(window.location.hash).toBe(`#/sprint/${queued.id}`);
  });

  it("inbox count badge turns amber when > 3 unfiled", () => {
    render(<Dashboard />);
    const badge = screen.getByTestId("inbox-count");
    expect(badge.textContent).toMatch(/4 unfiled/);
    expect(badge.getAttribute("data-warn")).toBe("true");
  });

  it("renders the inbox bullets with source lines", () => {
    render(<Dashboard />);
    for (const bullet of data.inbox.bullets) {
      expect(screen.getByText(bullet.text)).toBeInTheDocument();
      expect(screen.getByText(bullet.source)).toBeInTheDocument();
    }
  });
});
