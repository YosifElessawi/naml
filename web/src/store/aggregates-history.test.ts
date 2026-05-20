import { describe, expect, it, vi } from "vitest";

import { startAggregatesHistoryPoll } from "./aggregates-history.ts";
import { Store } from "./store.ts";

describe("startAggregatesHistoryPoll", () => {
  it("fetches and normalizes the history payload into the store", async () => {
    const store = new Store();
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          points: [
            {
              day: "2026-05-19",
              cost_usd: 1.23,
              tokens: 1000,
              sprints_started: 0,
              sprints_completed: 1,
              tier1_hit_count: 2,
              slice_fails: 0,
            },
          ],
        }),
    } as unknown as Response);

    const poller = startAggregatesHistoryPoll(store, {
      fetcher: fetcher as unknown as typeof fetch,
      refreshMs: 1_000_000,
      setInterval: () => 1,
      clearInterval: () => {},
      now: () => Date.parse("2026-05-20T00:00:00Z"),
    });
    await poller.fetchOnce();
    expect(fetcher).toHaveBeenCalledWith(
      "/api/aggregates-history?days=30",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
    const hist = store.getState().aggregates_history;
    expect(hist.points.length).toBe(1);
    expect(hist.points[0]?.day).toBe("2026-05-19");
    expect(hist.points[0]?.cost_usd).toBe(1.23);
    expect(hist.lastFetchedAt).toBe("2026-05-20T00:00:00.000Z");
  });

  it("does not crash if the response omits `points`", async () => {
    const store = new Store();
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({}),
    } as unknown as Response);
    const poller = startAggregatesHistoryPoll(store, {
      fetcher: fetcher as unknown as typeof fetch,
      refreshMs: 1_000_000,
      setInterval: () => 1,
      clearInterval: () => {},
    });
    const result = await poller.fetchOnce();
    expect(result?.points).toEqual([]);
  });
});
