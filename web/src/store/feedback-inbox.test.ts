import { describe, expect, it, vi } from "vitest";

import { startFeedbackInboxPoll } from "./feedback-inbox.ts";
import { Store } from "./store.ts";

function makeResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  } as unknown as Response);
}

describe("startFeedbackInboxPoll", () => {
  it("kicks an immediate fetch and patches the store", async () => {
    const store = new Store();
    const fetcher = vi.fn().mockImplementation(() =>
      makeResponse({
        unfiledCount: 4,
        bullets: [
          { id: "fb-1", text: "first", source: "inbox", addedAt: null },
          { id: "fb-2", text: "second", source: "inbox", addedAt: "2026-05-19" },
        ],
      }),
    );
    const poller = startFeedbackInboxPoll(store, {
      fetcher: fetcher as unknown as typeof fetch,
      // Big interval so the auto-tick doesn't fire during the test.
      intervalMs: 1_000_000,
      setInterval: () => 1,
      clearInterval: () => {},
      now: () => Date.parse("2026-05-20T00:00:00Z"),
    });
    // Allow the kick-off pollOnce to resolve.
    await poller.pollOnce();
    expect(fetcher).toHaveBeenCalledWith(
      "/api/feedback-inbox",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
    const inbox = store.getState().feedback_inbox;
    expect(inbox.unfiledCount).toBe(4);
    expect(inbox.bullets.length).toBe(2);
    expect(inbox.lastPolledAt).toBe("2026-05-20T00:00:00.000Z");
  });

  it("returns null on non-OK responses", async () => {
    const store = new Store();
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({}),
    } as unknown as Response);
    const poller = startFeedbackInboxPoll(store, {
      fetcher: fetcher as unknown as typeof fetch,
      intervalMs: 1_000_000,
      setInterval: () => 1,
      clearInterval: () => {},
    });
    const result = await poller.pollOnce();
    expect(result).toBeNull();
    expect(store.getState().feedback_inbox.bullets).toEqual([]);
  });

  it("survives fetch throwing", async () => {
    const store = new Store();
    const fetcher = vi.fn().mockRejectedValue(new Error("network down"));
    const poller = startFeedbackInboxPoll(store, {
      fetcher: fetcher as unknown as typeof fetch,
      intervalMs: 1_000_000,
      setInterval: () => 1,
      clearInterval: () => {},
    });
    const result = await poller.pollOnce();
    expect(result).toBeNull();
  });
});
