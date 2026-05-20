// FLIP (First-Last-Invert-Play) animation utility.
//
// When a slice card reorders (e.g. moves from lane-2 to lane-3 because its
// state advanced), the DOM mutation jumps it instantly. FLIP captures the
// element's bounding rect BEFORE the mutation (First) and AFTER (Last),
// then plays a transform from First→Last so the eye sees a smooth move.
//
// Usage:
//   const flip = new Flip(el => el.dataset.sliceId);
//   flip.capture(rootEl);
//   // …mutate the DOM (re-order children)…
//   flip.play(rootEl);
//
// The capture/play pair must run synchronously around the DOM mutation
// for the deltas to be meaningful. The util respects `prefers-reduced-motion`
// — if the user prefers reduced motion the play() is a no-op.

const REORDER_DURATION_MS = 300;
const REORDER_EASING = "ease-out";

type KeyFn = (el: HTMLElement) => string | undefined;

interface RectSnap {
  x: number;
  y: number;
}

function reducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export class Flip {
  private readonly keyFn: KeyFn;
  private snapshot = new Map<string, RectSnap>();
  /** Override hook for tests — defaults to reading `prefers-reduced-motion`. */
  reducedMotionOverride: boolean | null = null;

  constructor(keyFn: KeyFn) {
    this.keyFn = keyFn;
  }

  /** Snapshot every keyed child's bounding rect. Idempotent. */
  capture(root: HTMLElement): void {
    this.snapshot.clear();
    for (const child of this.iterChildren(root)) {
      const key = this.keyFn(child);
      if (key === undefined) continue;
      const rect = child.getBoundingClientRect();
      this.snapshot.set(key, { x: rect.left, y: rect.top });
    }
  }

  /**
   * Animate every previously-snapshotted child from its prior position
   * to its current one. Returns the number of children animated.
   */
  play(root: HTMLElement): number {
    const isReduced = this.reducedMotionOverride ?? reducedMotion();
    if (isReduced || this.snapshot.size === 0) {
      this.snapshot.clear();
      return 0;
    }

    let animated = 0;
    for (const child of this.iterChildren(root)) {
      const key = this.keyFn(child);
      if (key === undefined) continue;
      const prev = this.snapshot.get(key);
      if (!prev) continue;
      const rect = child.getBoundingClientRect();
      const dx = prev.x - rect.left;
      const dy = prev.y - rect.top;
      if (dx === 0 && dy === 0) continue;

      // Invert
      child.style.transition = "none";
      child.style.transform = `translate(${dx}px, ${dy}px)`;
      // Force a synchronous reflow so the inverted transform is committed
      // before the play transform is applied.
      void child.getBoundingClientRect();
      // Play
      child.style.transition = `transform ${REORDER_DURATION_MS}ms ${REORDER_EASING}`;
      child.style.transform = "";
      animated += 1;

      const cleanup = (ev: TransitionEvent) => {
        if (ev.propertyName !== "transform") return;
        child.style.transition = "";
        child.removeEventListener("transitionend", cleanup);
      };
      child.addEventListener("transitionend", cleanup);
    }

    this.snapshot.clear();
    return animated;
  }

  /** Drop the in-flight snapshot without playing — e.g. on unmount. */
  reset(): void {
    this.snapshot.clear();
  }

  private *iterChildren(root: HTMLElement): IterableIterator<HTMLElement> {
    for (let i = 0; i < root.children.length; i += 1) {
      const child = root.children.item(i);
      if (child instanceof HTMLElement) yield child;
    }
  }
}
