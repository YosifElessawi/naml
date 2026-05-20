// FLIP (First-Last-Invert-Play) animation utility.
//
// When a slice card reorders (e.g. moves from lane-2 to lane-3 because its
// state advanced), the DOM mutation jumps it instantly. FLIP captures the
// element's bounding rect BEFORE the mutation (First) and AFTER (Last),
// then plays a transform from First→Last so the eye sees a smooth move.
//
// Two usage patterns:
//
//   (1) Bracketed: explicit capture-before, play-after. Works in any
//       framework or vanilla JS. Mutates internal state.
//
//         const flip = new Flip(el => el.dataset.sliceId);
//         flip.capture(rootEl);
//         // …mutate the DOM (re-order children)…
//         flip.play(rootEl);
//
//   (2) Post-commit (React-friendly): the caller stashes the prior-commit
//       snapshot in a ref and asks Flip to animate from it after every
//       commit. No render-phase side effects, StrictMode-safe.
//
//         const prevRef = useRef<RectMap>(new Map());
//         useLayoutEffect(() => {
//           if (!rootRef.current) return;
//           flip.playFromSnapshot(rootRef.current, prevRef.current);
//           prevRef.current = flip.snapshot(rootRef.current);
//         });
//
// Both modes honour `prefers-reduced-motion` — play is a no-op when the
// user prefers reduced motion.

const REORDER_DURATION_MS = 300;
const REORDER_EASING = "ease-out";

type KeyFn = (el: HTMLElement) => string | undefined;

interface RectSnap {
  x: number;
  y: number;
}

/** Public alias for callers that hold their own snapshot ref. */
export type RectMap = Map<string, RectSnap>;

function reducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function* iterChildren(root: HTMLElement): IterableIterator<HTMLElement> {
  for (let i = 0; i < root.children.length; i += 1) {
    const child = root.children.item(i);
    if (child instanceof HTMLElement) yield child;
  }
}

export class Flip {
  private readonly keyFn: KeyFn;
  /** Internal snapshot used by the bracketed `capture()/play()` pair. */
  private internal: RectMap = new Map();
  /** Override hook for tests — defaults to reading `prefers-reduced-motion`. */
  reducedMotionOverride: boolean | null = null;

  constructor(keyFn: KeyFn) {
    this.keyFn = keyFn;
  }

  /** Pure: read every keyed child's bounding rect into a fresh map. */
  snapshot(root: HTMLElement): RectMap {
    const out: RectMap = new Map();
    for (const child of iterChildren(root)) {
      const key = this.keyFn(child);
      if (key === undefined) continue;
      const rect = child.getBoundingClientRect();
      out.set(key, { x: rect.left, y: rect.top });
    }
    return out;
  }

  /** Snapshot rects into the internal store (bracketed-mode API). */
  capture(root: HTMLElement): void {
    this.internal = this.snapshot(root);
  }

  /**
   * Animate every child from its prior rect (stored internally by
   * `capture()`) to its current rect. Returns the count of animated
   * children.
   */
  play(root: HTMLElement): number {
    const animated = this.playFromSnapshot(root, this.internal);
    this.internal = new Map();
    return animated;
  }

  /**
   * Post-commit API: animate every child from the `prev` rect map to
   * the current DOM rect. Pure with respect to `Flip`'s internal state —
   * the caller owns `prev`. Returns the count of animated children.
   */
  playFromSnapshot(root: HTMLElement, prev: RectMap): number {
    const isReduced = this.reducedMotionOverride ?? reducedMotion();
    if (isReduced || prev.size === 0) return 0;

    let animated = 0;
    for (const child of iterChildren(root)) {
      const key = this.keyFn(child);
      if (key === undefined) continue;
      const prevRect = prev.get(key);
      if (!prevRect) continue;
      const rect = child.getBoundingClientRect();
      const dx = prevRect.x - rect.left;
      const dy = prevRect.y - rect.top;
      if (dx === 0 && dy === 0) continue;

      // Invert: place the child back at its prior position with no
      // transition, then force a reflow so the inverse transform is
      // committed.
      child.style.transition = "none";
      child.style.transform = `translate(${dx}px, ${dy}px)`;
      void child.getBoundingClientRect();
      // Play: clear the transform; the transition carries the element
      // back to its real position over REORDER_DURATION_MS.
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

    return animated;
  }

  /** Drop the internal snapshot without playing — e.g. on unmount. */
  reset(): void {
    this.internal = new Map();
  }
}
