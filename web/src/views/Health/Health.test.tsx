import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Health } from "./Health.tsx";
import { FIXTURE_HEALTH } from "./fixtures.ts";

describe("Health", () => {
  it("renders one trend card per fixture entry plus the bar card", () => {
    render(<Health />);
    for (const card of FIXTURE_HEALTH.trends) {
      expect(screen.getByText(card.label)).toBeInTheDocument();
    }
    for (const card of FIXTURE_HEALTH.bars) {
      expect(screen.getByText(card.label)).toBeInTheDocument();
    }
  });

  it("renders SVG charts with axis lines for every trend card", () => {
    const { container } = render(<Health />);
    const charts = container.querySelectorAll("svg");
    // 5 trend cards + 1 bar card = 6 charts.
    expect(charts.length).toBe(FIXTURE_HEALTH.trends.length + FIXTURE_HEALTH.bars.length);
    const polylines = container.querySelectorAll("polyline");
    expect(polylines.length).toBe(FIXTURE_HEALTH.trends.length);
  });

  it("renders the follow-up analytics row", () => {
    render(<Health />);
    for (const item of FIXTURE_HEALTH.followups) {
      expect(screen.getByText(`${item.label}:`)).toBeInTheDocument();
    }
  });

  it("uses an amber band on the warn-toned chart", () => {
    const { container } = render(<Health />);
    // slice-fail-rate is the only fixture with an amberAt threshold.
    const warnCard = container.querySelector('[data-card-id="slice-fail-rate"]');
    expect(warnCard).not.toBeNull();
    expect(warnCard?.querySelector("rect")).not.toBeNull();
  });
});
