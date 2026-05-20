import { describe, expect, it, vi } from "vitest";

import {
  intervene,
  isTerminalUnlocked,
  TERMINAL_UNLOCKED_STATES,
} from "./intervene.ts";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("intervene fetch wiring", () => {
  it("POSTs the right URL with action query param", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, detail: "queued" }));
    const result = await intervene("slice-2", "hold", { fetch: fetchMock });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/intervene/slice-2?action=hold");
    expect((init as RequestInit).method).toBe("POST");
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.action).toBe("hold");
      expect(result.detail).toBe("queued");
    }
  });

  it("maps 409 conflicts to InterveneFailure with current_state", async () => {
    const fetchMock = vi.fn(
      async () =>
        jsonResponse(
          { error: "slice is in 'work'", current_state: "work" },
          409,
        ),
    );
    const result = await intervene("slice-1", "open-terminal", { fetch: fetchMock });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(409);
      expect(result.error).toContain("work");
      expect(result.currentState).toBe("work");
    }
  });

  it("handles network failures without throwing", async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const result = await intervene("slice-1", "hold", { fetch: fetchMock });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(0);
      expect(result.error).toBe("ECONNREFUSED");
    }
  });

  it("handles 500s with a sane fallback message when body is not JSON", async () => {
    const fetchMock = vi.fn(
      async () => new Response("oops", { status: 500 }),
    );
    const result = await intervene("slice-1", "hold", { fetch: fetchMock });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(500);
      expect(result.error).toMatch(/HTTP 500/);
    }
  });

  it("encodes slice ids safely so query injection is impossible", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    await intervene("../etc/passwd", "hold", { fetch: fetchMock });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("..%2Fetc%2Fpasswd");
  });
});

describe("TERMINAL_UNLOCKED_STATES", () => {
  it("includes held — the whole point of slice-14", () => {
    expect(TERMINAL_UNLOCKED_STATES.has("held")).toBe(true);
    expect(isTerminalUnlocked("held")).toBe(true);
  });

  it("excludes in-flight states so the drawer can't open a session naml owns", () => {
    expect(isTerminalUnlocked("work")).toBe(false);
    expect(isTerminalUnlocked("pr")).toBe(false);
    expect(isTerminalUnlocked("setup")).toBe(false);
    expect(isTerminalUnlocked("pending")).toBe(false);
  });

  it("includes resting states once the lane has handed off", () => {
    for (const s of ["review", "merged", "failed", "abandoned"]) {
      expect(isTerminalUnlocked(s)).toBe(true);
    }
  });
});
