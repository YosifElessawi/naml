import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Store } from "../../store/store.ts";
import { CostTimeline } from "./CostTimeline.tsx";

/** Test-friendly tween that fires the final value synchronously.
 * Matches slice-12's tween signature: (from, to, onUpdate, options?). */
const syncTween: typeof import("../../lib/tween.ts").tween = (_from, to, onUpdate) => {
  onUpdate(to);
  return () => {};
};

describe("CostTimeline", () => {
  it("renders all four cost windows with their initial values", () => {
    const store = new Store();
    act(() => {
      store.patch("project_metrics", {
        ...store.getState().project_metrics,
        today_cost: 4.21,
        week_cost: 24.8,
        last_30d_cost: 118.42,
        lifetime_cost: 342.16,
      });
    });
    render(<CostTimeline store={store} tweenFn={syncTween} />);
    expect(screen.getByTestId("cost-today_cost").textContent).toBe("$4.21");
    expect(screen.getByTestId("cost-week_cost").textContent).toBe("$24.8");
    expect(screen.getByTestId("cost-last_30d_cost").textContent).toBe("$118.4");
    expect(screen.getByTestId("cost-lifetime_cost").textContent).toBe("$342.2");
  });

  it("marks lifetime cell with data-lifetime='true' for the cyan tint", () => {
    const store = new Store();
    render(<CostTimeline store={store} tweenFn={syncTween} />);
    const lifetime = screen.getByTestId("cost-lifetime_cost").closest("li");
    expect(lifetime?.getAttribute("data-lifetime")).toBe("true");
    const today = screen.getByTestId("cost-today_cost").closest("li");
    expect(today?.getAttribute("data-lifetime")).toBe("false");
  });

  it("tweens the counter when a value changes", () => {
    const store = new Store();
    const tweenFn = vi.fn(syncTween);
    render(<CostTimeline store={store} tweenFn={tweenFn} />);
    expect(screen.getByTestId("cost-today_cost").textContent).toBe("$0.00");

    act(() => {
      store.patch("project_metrics", {
        ...store.getState().project_metrics,
        today_cost: 1.23,
      });
    });

    expect(tweenFn).toHaveBeenCalled();
    expect(screen.getByTestId("cost-today_cost").textContent).toBe("$1.23");
  });

  it("formats >$1k values with thousand separators", () => {
    const store = new Store();
    act(() => {
      store.patch("project_metrics", {
        ...store.getState().project_metrics,
        lifetime_cost: 12345.67,
      });
    });
    render(<CostTimeline store={store} tweenFn={syncTween} />);
    expect(screen.getByTestId("cost-lifetime_cost").textContent).toBe("$12,346");
  });
});
