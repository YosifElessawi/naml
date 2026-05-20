// Tests for the EventSource wrapper. jsdom does not implement
// EventSource, so we hand the wrapper a mock constructor that lets us
// fire arbitrary events at will. The mock mirrors the bits of the native
// API we use: addEventListener("open"|"error"|<custom>), close(), and
// MessageEvent.lastEventId.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createStore } from "../store";
import type { StoreState } from "../store/types";

import { LOST_AFTER_MS, NamlEventSource, SLOW_AFTER_MS } from "./EventSource";

type Handler = (ev: MessageEvent) => void;
type OpenErrorHandler = () => void;

class MockEventSource {
  static lastInstance: MockEventSource | null = null;

  public readonly url: string;
  public closed = false;
  private readonly handlers = new Map<string, Set<Handler | OpenErrorHandler>>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.lastInstance = this;
  }

  addEventListener(type: string, fn: Handler | OpenErrorHandler): void {
    let set = this.handlers.get(type);
    if (!set) {
      set = new Set();
      this.handlers.set(type, set);
    }
    set.add(fn);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data: unknown, id?: number | string): void {
    const set = this.handlers.get(type);
    if (!set) return;
    const ev = {
      type,
      data: typeof data === "string" ? data : JSON.stringify(data),
      lastEventId: id !== undefined ? String(id) : "",
    } as unknown as MessageEvent;
    for (const fn of set) (fn as Handler)(ev);
  }

  emitBare(type: "open" | "error"): void {
    const set = this.handlers.get(type);
    if (!set) return;
    for (const fn of set) (fn as OpenErrorHandler)();
  }
}

describe("NamlEventSource", () => {
  beforeEach(() => {
    MockEventSource.lastInstance = null;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function makeES(store = createStore()) {
    const es = new NamlEventSource(store, {
      url: "/events",
      EventSourceCtor: MockEventSource as unknown as typeof EventSource,
      now: () => Date.now(),
      syncTickMs: 50,
    });
    return { es, store };
  }

  it("connects on start and reports `connecting`", () => {
    const { es, store } = makeES();
    es.start();
    expect(store.getState().syncStatus).toBe("connecting");
    expect(MockEventSource.lastInstance?.url).toBe("/events");
    es.stop();
  });

  it("applies a snapshot payload to the store and flips to `connected`", () => {
    const { es, store } = makeES();
    es.start();

    const snap = {
      ts: "2026-05-20T00:00:00Z",
      sprints: { "sprint-A": { sprint_id: "sprint-A", state: "executing" } },
      slices: {
        "sprint-A::slice-1": { slice_id: "slice-1", state: "work" },
      },
      aggregates: { lifetime: { cost_usd: 0 } },
    };
    MockEventSource.lastInstance?.emit("snapshot", snap, 5);

    const s = store.getState();
    expect(s.syncStatus).toBe("connected");
    expect(s.sprints["sprint-A"]?.state).toBe("executing");
    expect(s.slices["sprint-A::slice-1"]?.state).toBe("work");
    expect(s.lastEventId).toBe(5);
    es.stop();
  });

  it("applies a state-update delta", () => {
    const { es, store } = makeES();
    es.start();
    // Seed with a snapshot.
    MockEventSource.lastInstance?.emit(
      "snapshot",
      {
        ts: "x",
        sprints: {},
        slices: { "sprint-A::slice-1": { slice_id: "slice-1", state: "work" } },
        aggregates: {},
      },
      1,
    );
    MockEventSource.lastInstance?.emit(
      "state-update",
      {
        kind: "slice",
        id: "sprint-A::slice-1",
        delta: { slice_id: "slice-1", state: "pr" },
      },
      2,
    );

    expect(store.getState().slices["sprint-A::slice-1"]?.state).toBe("pr");
    expect(store.getState().lastEventId).toBe(2);
    es.stop();
  });

  it("flips to `slow` then `lost` as time-since-last-event grows", () => {
    vi.useFakeTimers();
    const store = createStore();
    const es = new NamlEventSource(store, {
      url: "/events",
      EventSourceCtor: MockEventSource as unknown as typeof EventSource,
      now: () => Date.now(),
      syncTickMs: 10,
    });
    es.start();

    // First, get the store out of `connecting`.
    MockEventSource.lastInstance?.emit("ping", { t: "x" }, 1);
    expect(store.getState().syncStatus).toBe("connected");

    vi.advanceTimersByTime(SLOW_AFTER_MS + 50);
    expect(store.getState().syncStatus).toBe("slow");

    vi.advanceTimersByTime(LOST_AFTER_MS);
    expect(store.getState().syncStatus).toBe("lost");
    es.stop();
  });

  it("notifies key subscribers when syncStatus changes", () => {
    const { es, store } = makeES();
    const seen: StoreState["syncStatus"][] = [];
    store.subscribeKey("syncStatus", (status) => seen.push(status));
    es.start();
    MockEventSource.lastInstance?.emit("ping", { t: "x" }, 1);
    expect(seen).toContain("connected");
    es.stop();
  });

  it("stop() closes the underlying connection and marks lost", () => {
    const { es, store } = makeES();
    es.start();
    const inner = MockEventSource.lastInstance;
    es.stop();
    expect(inner?.closed).toBe(true);
    expect(store.getState().syncStatus).toBe("lost");
  });

  it("forceReconnect closes the current ES and opens a fresh one", () => {
    const { es } = makeES();
    es.start();
    const first = MockEventSource.lastInstance;
    es.forceReconnect();
    const second = MockEventSource.lastInstance;
    expect(first?.closed).toBe(true);
    expect(second).not.toBe(first);
    es.stop();
  });

  it("drops malformed payloads instead of throwing", () => {
    const { es, store } = makeES();
    es.start();
    // Pass already-stringified garbage that won't parse as JSON.
    MockEventSource.lastInstance?.emit("state-update", "{not-json", 1);
    // Store should not have been touched (no slices recorded).
    expect(Object.keys(store.getState().slices)).toHaveLength(0);
    es.stop();
  });
});
