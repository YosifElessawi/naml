import { describe, expect, it, vi } from "vitest";

import { TERMINAL_UNLOCKED_STATES, intervene, isTerminalUnlocked } from "./intervene.ts";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// Tightly-typed fetch mock factory. `vi.fn(async () => …)` infers the
// implementation as a zero-arg function, which then types `mock.calls` as
// `[][]` and breaks destructuring under `noUncheckedIndexedAccess`. Casting
// through `typeof fetch` gives us the right shape both for the call site
// (the production code passes a real URL + init) and for the assertions.
function fetchMockReturning(impl: () => Promise<Response> | never) {
  return vi.fn<typeof fetch>(impl as unknown as typeof fetch);
}

describe("intervene fetch wiring", () => {
  it("POSTs the right URL with action query param", async () => {
    const fetchMock = fetchMockReturning(async () => jsonResponse({ ok: true, detail: "queued" }));
    const result = await intervene("slice-2", "hold", { fetch: fetchMock });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchMock.mock.calls[0];
    expect(call).toBeDefined();
    if (!call) return;
    const [url, init] = call;
    expect(url).toBe("/intervene/slice-2?action=hold");
    expect(init?.method).toBe("POST");
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.action).toBe("hold");
      expect(result.detail).toBe("queued");
    }
  });

  it("maps 409 conflicts to InterveneFailure with current_state", async () => {
    const fetchMock = fetchMockReturning(async () =>
      jsonResponse({ error: "slice is in 'work'", current_state: "work" }, 409),
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
    const fetchMock = fetchMockReturning(async () => {
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
    const fetchMock = fetchMockReturning(async () => new Response("oops", { status: 500 }));
    const result = await intervene("slice-1", "hold", { fetch: fetchMock });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(500);
      expect(result.error).toMatch(/HTTP 500/);
    }
  });

  it("encodes slice ids safely so query injection is impossible", async () => {
    const fetchMock = fetchMockReturning(async () => jsonResponse({ ok: true }));
    await intervene("../etc/passwd", "hold", { fetch: fetchMock });

    const call = fetchMock.mock.calls[0];
    expect(call).toBeDefined();
    if (!call) return;
    const [url] = call;
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
