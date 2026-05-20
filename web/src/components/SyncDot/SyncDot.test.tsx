import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Store } from "../../store/store.ts";
import { SyncDot, syncTagline } from "./SyncDot.tsx";

describe("SyncDot", () => {
  it("renders the connecting label by default", () => {
    const store = new Store();
    render(<SyncDot store={store} now={() => Date.parse("2026-05-20T00:00:00Z")} />);
    expect(screen.getByRole("status").getAttribute("data-sync")).toBe("connecting");
    expect(screen.getByRole("status").textContent).toMatch(/reconnecting/i);
  });

  it("flips to `live` and renders the Synced label when an event lands", () => {
    const store = new Store();
    const now = () => Date.parse("2026-05-20T00:00:10Z");
    render(<SyncDot store={store} now={now} />);

    act(() => {
      store.patch("lastEventAt", "2026-05-20T00:00:07Z");
      store.patch("syncStatus", "live");
    });

    const status = screen.getByRole("status");
    expect(status.getAttribute("data-sync")).toBe("live");
    expect(status.textContent).toMatch(/Synced 3s ago/);
  });

  it("renders each of the 4 states correctly", () => {
    for (const status of ["live", "slow", "lost", "connecting"] as const) {
      const store = new Store();
      const { unmount } = render(<SyncDot store={store} now={() => Date.now()} />);
      act(() => {
        store.patch("syncStatus", status);
      });
      expect(screen.getByRole("status").getAttribute("data-sync")).toBe(status);
      unmount();
    }
  });

  it("compact mode renders dot-only with aria-label", () => {
    const store = new Store();
    act(() => store.patch("syncStatus", "slow"));
    const { container } = render(<SyncDot store={store} compact now={() => Date.now()} />);
    const dot = container.querySelector(".naml-sync-dot");
    expect(dot).not.toBeNull();
    expect(dot?.getAttribute("data-sync")).toBe("slow");
    expect(dot?.getAttribute("aria-label")).toMatch(/synced|sync lost|reconnecting/i);
  });

  it("syncTagline produces the right label per state", () => {
    const baseStore = new Store();
    const state = baseStore.getState();

    expect(
      syncTagline({ ...state, syncStatus: "lost", lastEventAt: "2026-05-20T00:00:00Z" }, () =>
        Date.parse("2026-05-20T00:00:30Z"),
      ),
    ).toMatch(/Sync lost/);

    expect(
      syncTagline({ ...state, syncStatus: "connecting", reconnectAttempt: 3 }, () => Date.now()),
    ).toMatch(/reconnecting attempt 3/);
  });
});
