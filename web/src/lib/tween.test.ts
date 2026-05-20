import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { easeOutCubic, linear, tween } from "./tween";

interface ScheduledFrame {
  cb: (t: number) => void;
}

function harness(): {
  raf: (cb: (t: number) => void) => number;
  cancelRaf: (h: number) => void;
  now: () => number;
  advance: (deltaMs: number) => void;
  step: () => boolean;
  pending: () => number;
} {
  let clock = 0;
  let nextHandle = 1;
  const scheduled = new Map<number, ScheduledFrame>();
  return {
    raf(cb) {
      const h = nextHandle++;
      scheduled.set(h, { cb });
      return h;
    },
    cancelRaf(h) {
      scheduled.delete(h);
    },
    now() {
      return clock;
    },
    advance(deltaMs) {
      clock += deltaMs;
    },
    step() {
      const next = scheduled.keys().next();
      if (next.done) return false;
      const handle = next.value;
      const frame = scheduled.get(handle);
      scheduled.delete(handle);
      frame?.cb(clock);
      return true;
    },
    pending() {
      return scheduled.size;
    },
  };
}

describe("tween", () => {
  it("starts at from on the first frame and ends exactly at to", () => {
    const h = harness();
    const seen: number[] = [];
    tween(0, 100, (v) => seen.push(v), {
      durationMs: 200,
      easing: linear,
      raf: h.raf,
      cancelRaf: h.cancelRaf,
      now: h.now,
    });
    // First frame at t=0: progress=0 → value=0
    h.step();
    expect(seen[0]).toBe(0);
    // Mid-way: linear easing at 50% → 50
    h.advance(100);
    h.step();
    expect(seen[1]).toBeCloseTo(50, 5);
    // At the end: value clamps to ``to`` exactly
    h.advance(100);
    h.step();
    expect(seen.at(-1)).toBe(100);
    expect(h.pending()).toBe(0);
  });

  it("respects custom durationMs (defaults to 600ms otherwise)", () => {
    const h = harness();
    let last = 0;
    tween(
      0,
      60,
      (v) => {
        last = v;
      },
      {
        raf: h.raf,
        cancelRaf: h.cancelRaf,
        now: h.now,
        easing: linear,
      },
    );
    h.step(); // t=0
    h.advance(300); // 50% of default 600ms
    h.step();
    expect(last).toBeCloseTo(30, 5);
  });

  it("applies ease-out cubic by default (faster early, slower late)", () => {
    const h = harness();
    const seen: number[] = [];
    tween(0, 100, (v) => seen.push(v), {
      durationMs: 100,
      raf: h.raf,
      cancelRaf: h.cancelRaf,
      now: h.now,
    });
    h.step(); // t=0 → 0
    h.advance(50); // midpoint
    h.step();
    // Ease-out cubic at 0.5 = 1 - 0.5^3 = 0.875 → 87.5
    expect(seen[1]).toBeCloseTo(87.5, 5);
    h.advance(50);
    h.step(); // landing
    expect(seen.at(-1)).toBe(100);
  });

  it("supports linear easing (sanity for reduced-motion fallbacks)", () => {
    expect(linear(0)).toBe(0);
    expect(linear(0.5)).toBe(0.5);
    expect(linear(1)).toBe(1);
  });

  it("ease-out cubic monotonically increases and lands at 1", () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
    expect(easeOutCubic(0.5)).toBeGreaterThan(0.5);
    // Strictly increasing
    let prev = Number.NEGATIVE_INFINITY;
    for (let t = 0; t <= 1; t += 0.05) {
      const v = easeOutCubic(t);
      expect(v).toBeGreaterThan(prev);
      prev = v;
    }
  });

  it("calls onComplete once when the tween finishes", () => {
    const h = harness();
    const onComplete = vi.fn();
    tween(0, 50, () => {}, {
      durationMs: 100,
      easing: linear,
      raf: h.raf,
      cancelRaf: h.cancelRaf,
      now: h.now,
      onComplete,
    });
    h.step(); // initial frame
    h.advance(100);
    h.step(); // completion frame
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("cancel() halts the tween before completion", () => {
    const h = harness();
    let last = -1;
    const cancel = tween(
      0,
      100,
      (v) => {
        last = v;
      },
      {
        durationMs: 200,
        easing: linear,
        raf: h.raf,
        cancelRaf: h.cancelRaf,
        now: h.now,
      },
    );
    h.step(); // initial frame at 0
    cancel();
    // After cancel, the next scheduled frame should be wiped out.
    expect(h.pending()).toBe(0);
    h.advance(200);
    expect(last).toBe(0);
  });

  it("short-circuits when durationMs is 0", () => {
    const h = harness();
    const seen: number[] = [];
    tween(10, 42, (v) => seen.push(v), {
      durationMs: 0,
      raf: h.raf,
      cancelRaf: h.cancelRaf,
      now: h.now,
    });
    h.step();
    expect(seen).toEqual([42]);
    expect(h.pending()).toBe(0);
  });

  it("falls back to setTimeout when raf is unavailable", async () => {
    const realRaf = globalThis.requestAnimationFrame;
    // @ts-expect-error — intentional un-binding for test
    globalThis.requestAnimationFrame = undefined;
    const seen: number[] = [];
    tween(0, 50, (v) => seen.push(v), {
      durationMs: 0,
      easing: linear,
    });
    await new Promise((r) => setTimeout(r, 32));
    expect(seen.length).toBeGreaterThanOrEqual(1);
    expect(seen.at(-1)).toBe(50);
    globalThis.requestAnimationFrame = realRaf;
  });
});

describe("tween default duration", () => {
  let realRaf: typeof globalThis.requestAnimationFrame;
  let realCancel: typeof globalThis.cancelAnimationFrame;
  let queued: Array<(t: number) => void>;

  beforeEach(() => {
    realRaf = globalThis.requestAnimationFrame;
    realCancel = globalThis.cancelAnimationFrame;
    queued = [];
    globalThis.requestAnimationFrame = ((cb: (t: number) => void) => {
      queued.push(cb);
      return queued.length;
    }) as typeof globalThis.requestAnimationFrame;
    globalThis.cancelAnimationFrame = (() => {}) as typeof globalThis.cancelAnimationFrame;
  });

  afterEach(() => {
    globalThis.requestAnimationFrame = realRaf;
    globalThis.cancelAnimationFrame = realCancel;
  });

  it("defaults durationMs to 600 when omitted", () => {
    const seen: number[] = [];
    let now = 0;
    tween(0, 600, (v) => seen.push(v), {
      easing: linear,
      now: () => now,
    });
    // Step the first frame.
    queued.shift()?.(now);
    expect(seen[0]).toBe(0);
    // At ~300ms (half), value should be ~300 under linear easing.
    now = 300;
    queued.shift()?.(now);
    expect(seen[1]).toBeCloseTo(300, 5);
  });
});
