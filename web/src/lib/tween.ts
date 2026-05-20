/**
 * Counter tween — smoothly animate a numeric value from ``from`` to ``to``
 * over ``durationMs`` using ``requestAnimationFrame``.
 *
 * Used by every cost / token counter in the cockpit so a ``metric-tick``
 * doesn't pop the number; it ticks up visibly. ~600ms ease-out gives the
 * "casino counter" feel called out in artifacts/spec.md Q8.
 *
 * Returns a cancel function. Calling it before the tween completes stops
 * the animation in place — useful when a new value lands mid-tween and
 * you want to start a fresh tween from the current visible value.
 */

export type Easing = (t: number) => number;
export type TweenUpdate = (value: number) => void;
export type TweenCancel = () => void;

/** Default easing — cubic ease-out. ``t`` is the normalised [0, 1] progress. */
export const easeOutCubic: Easing = (t) => {
  const inv = 1 - t;
  return 1 - inv * inv * inv;
};

/** Linear easing, exported for tests + reduced-motion fallbacks. */
export const linear: Easing = (t) => t;

export interface TweenOptions {
  /** Total animation duration in milliseconds. Defaults to 600ms. */
  durationMs?: number;
  /** Easing function. Defaults to ``easeOutCubic``. */
  easing?: Easing;
  /** Optional callback fired when the tween reaches its target. */
  onComplete?: () => void;
  /**
   * Injectable scheduler — defaults to ``window.requestAnimationFrame``.
   * Tests pass a fake to drive frames synchronously without faking time.
   */
  raf?: (cb: (timestamp: number) => void) => number;
  /** Matching cancel for the injected scheduler. */
  cancelRaf?: (handle: number) => void;
  /**
   * Injectable clock for the start timestamp. Defaults to
   * ``performance.now()``. Tests pass a counter so they can compute exact
   * progress per frame.
   */
  now?: () => number;
}

const DEFAULT_DURATION_MS = 600;

/**
 * Start a tween. The first call to ``onUpdate`` is the initial ``from``
 * value on the next frame; subsequent calls march the value toward ``to``;
 * the final call delivers exactly ``to`` and the tween is done.
 *
 * If ``from === to`` the tween short-circuits to a single ``onUpdate(to)``
 * on the next frame and resolves.
 */
export function tween(
  from: number,
  to: number,
  onUpdate: TweenUpdate,
  options: TweenOptions = {},
): TweenCancel {
  const duration = options.durationMs ?? DEFAULT_DURATION_MS;
  const easing = options.easing ?? easeOutCubic;
  const raf = options.raf ?? defaultRaf();
  const cancelRaf = options.cancelRaf ?? defaultCancelRaf();
  const now = options.now ?? defaultNow();

  let cancelled = false;
  let handle = 0;
  const start = now();
  const delta = to - from;

  const frame = (): void => {
    if (cancelled) return;
    const elapsed = now() - start;
    if (duration <= 0 || elapsed >= duration) {
      onUpdate(to);
      options.onComplete?.();
      return;
    }
    const progress = elapsed / duration;
    const value = from + delta * easing(progress);
    onUpdate(value);
    handle = raf(frame);
  };

  handle = raf(frame);

  return () => {
    cancelled = true;
    if (handle) {
      cancelRaf(handle);
    }
  };
}

function defaultRaf(): (cb: (t: number) => void) => number {
  if (typeof globalThis.requestAnimationFrame === "function") {
    return globalThis.requestAnimationFrame.bind(globalThis);
  }
  // Non-browser environments (SSR, Node tests without jsdom rAF polyfill):
  // schedule on setTimeout at ~60fps so callers don't have to care.
  return (cb) => setTimeout(() => cb(Date.now()), 16) as unknown as number;
}

function defaultCancelRaf(): (handle: number) => void {
  if (typeof globalThis.cancelAnimationFrame === "function") {
    return globalThis.cancelAnimationFrame.bind(globalThis);
  }
  return (handle) => clearTimeout(handle as unknown as ReturnType<typeof setTimeout>);
}

function defaultNow(): () => number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return () => performance.now();
  }
  return () => Date.now();
}
