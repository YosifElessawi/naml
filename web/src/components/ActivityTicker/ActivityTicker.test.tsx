import { act, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Store } from "../../store/store.ts";
import { recordTransition } from "../../store/transitions.ts";
import { ActivityTicker } from "./ActivityTicker.tsx";

describe("ActivityTicker", () => {
  it("renders the empty hint when no transitions have arrived", () => {
    const store = new Store();
    render(<ActivityTicker store={store} now={() => Date.now()} />);
    expect(screen.getByText(/no state changes yet/i)).toBeInTheDocument();
  });

  it("renders most-recent-first, capped at `limit`", () => {
    const store = new Store();
    act(() => {
      recordTransition(store, {
        kind: "slice",
        targetId: "slice-1",
        fromState: "pending",
        toState: "setup",
        at: "2026-05-20T00:00:00Z",
      });
      recordTransition(store, {
        kind: "slice",
        targetId: "slice-2",
        fromState: "setup",
        toState: "work",
        at: "2026-05-20T00:00:01Z",
      });
      recordTransition(store, {
        kind: "slice",
        targetId: "slice-3",
        fromState: "work",
        toState: "pr",
        at: "2026-05-20T00:00:02Z",
      });
    });
    render(
      <ActivityTicker store={store} limit={2} now={() => Date.parse("2026-05-20T00:00:05Z")} />,
    );
    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    expect(items.length).toBe(2);
    // Most-recent first
    expect(items[0]?.textContent).toContain("slice-3");
    expect(items[1]?.textContent).toContain("slice-2");
  });

  it("re-renders when a new transition lands", () => {
    const store = new Store();
    render(<ActivityTicker store={store} now={() => Date.now()} />);
    expect(screen.queryByRole("list")).toBeNull();

    act(() => {
      recordTransition(store, {
        kind: "sprint",
        targetId: "sprint-1",
        fromState: "executing",
        toState: "merging",
        at: new Date().toISOString(),
      });
    });

    const list = screen.getByRole("list");
    const item = within(list).getByRole("listitem");
    expect(item.textContent).toContain("sprint-1");
    expect(item.textContent).toContain("executing → merging");
    expect(item.className).toContain("naml-anim-ticker-entry");
  });

  it("includes the age label on each entry", () => {
    const store = new Store();
    act(() => {
      recordTransition(store, {
        kind: "slice",
        targetId: "slice-4",
        fromState: null,
        toState: "setup",
        at: "2026-05-20T00:00:00Z",
      });
    });
    render(<ActivityTicker store={store} now={() => Date.parse("2026-05-20T00:00:08Z")} />);
    expect(screen.getByText(/8s ago/)).toBeInTheDocument();
  });
});
