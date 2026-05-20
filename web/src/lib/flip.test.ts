import { beforeEach, describe, expect, it } from "vitest";

import { Flip } from "./flip.ts";

function appendChild(root: HTMLElement, id: string): HTMLElement {
  const el = document.createElement("div");
  el.dataset.sliceId = id;
  el.textContent = id;
  root.appendChild(el);
  return el;
}

// jsdom doesn't run layout, so we stub getBoundingClientRect to expose
// the orderings we want. The rect's `left` encodes the child's logical
// position; the FLIP util only cares about deltas.
function stubRect(el: HTMLElement, left: number, top = 0) {
  el.getBoundingClientRect = () =>
    ({
      x: left,
      y: top,
      width: 100,
      height: 50,
      left,
      right: left + 100,
      top,
      bottom: top + 50,
      toJSON() {},
    }) as DOMRect;
}

describe("Flip", () => {
  let root: HTMLElement;

  beforeEach(() => {
    document.body.innerHTML = "";
    root = document.createElement("div");
    document.body.appendChild(root);
  });

  it("animates moved children with inverse-then-play", () => {
    const a = appendChild(root, "a");
    const b = appendChild(root, "b");
    stubRect(a, 0);
    stubRect(b, 200);

    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = false;
    flip.capture(root);

    // Mutate: swap a and b
    root.removeChild(a);
    root.appendChild(a);
    stubRect(b, 0);
    stubRect(a, 200);

    const animated = flip.play(root);
    expect(animated).toBe(2);
    // After play() the inline transform is reset (the eased transition
    // back to identity is in-flight, ending at "").
    expect(a.style.transform).toBe("");
    expect(b.style.transform).toBe("");
  });

  it("respects `prefers-reduced-motion`", () => {
    const a = appendChild(root, "a");
    stubRect(a, 0);
    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = true;
    flip.capture(root);
    stubRect(a, 200);
    expect(flip.play(root)).toBe(0);
  });

  it("ignores children with no key", () => {
    const a = appendChild(root, "a");
    const orphan = document.createElement("div");
    root.appendChild(orphan);
    stubRect(a, 0);
    stubRect(orphan, 100);

    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = false;
    flip.capture(root);
    stubRect(a, 50);
    stubRect(orphan, 200);
    // Only `a` should animate; the orphan is keyless.
    expect(flip.play(root)).toBe(1);
  });

  it("reset() drops the snapshot without playing", () => {
    const a = appendChild(root, "a");
    stubRect(a, 0);
    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = false;
    flip.capture(root);
    flip.reset();
    stubRect(a, 200);
    expect(flip.play(root)).toBe(0);
  });

  it("skips children that didn't move", () => {
    const a = appendChild(root, "a");
    stubRect(a, 100);
    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = false;
    flip.capture(root);
    stubRect(a, 100);
    expect(flip.play(root)).toBe(0);
  });

  it("snapshot() returns a plain map without mutating internal state", () => {
    const a = appendChild(root, "a");
    const b = appendChild(root, "b");
    stubRect(a, 0);
    stubRect(b, 100);

    const flip = new Flip((el) => el.dataset.sliceId);
    const snap = flip.snapshot(root);
    expect(snap.get("a")?.x).toBe(0);
    expect(snap.get("b")?.x).toBe(100);
    // Internal snapshot stays empty — caller owns the returned map.
    expect(flip.play(root)).toBe(0);
  });

  it("playFromSnapshot animates from a caller-owned prev map", () => {
    const a = appendChild(root, "a");
    const b = appendChild(root, "b");
    stubRect(a, 0);
    stubRect(b, 200);

    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = false;
    const prev = flip.snapshot(root);

    stubRect(a, 200);
    stubRect(b, 0);

    expect(flip.playFromSnapshot(root, prev)).toBe(2);
    expect(a.style.transform).toBe("");
    expect(b.style.transform).toBe("");
  });

  it("playFromSnapshot is a no-op when prev is empty", () => {
    const a = appendChild(root, "a");
    stubRect(a, 0);
    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = false;
    expect(flip.playFromSnapshot(root, new Map())).toBe(0);
  });

  it("playFromSnapshot respects prefers-reduced-motion", () => {
    const a = appendChild(root, "a");
    stubRect(a, 0);
    const flip = new Flip((el) => el.dataset.sliceId);
    const prev = flip.snapshot(root);
    flip.reducedMotionOverride = true;
    stubRect(a, 200);
    expect(flip.playFromSnapshot(root, prev)).toBe(0);
  });
});
