import { describe, expect, it } from "vitest";

import { Store } from "./store.ts";
import { clearTransitions, recordTransition } from "./transitions.ts";
import { TRANSITIONS_CAP } from "./types.ts";

describe("transitions", () => {
  it("prepends new transitions and caps the buffer", () => {
    const s = new Store();
    for (let i = 0; i < TRANSITIONS_CAP + 5; i += 1) {
      recordTransition(s, {
        kind: "slice",
        targetId: `slice-${i}`,
        fromState: "work",
        toState: "review",
        at: new Date(i * 1000).toISOString(),
      });
    }
    const transitions = s.getState().transitions;
    expect(transitions.length).toBe(TRANSITIONS_CAP);
    expect(transitions[0]?.targetId).toBe(`slice-${TRANSITIONS_CAP + 4}`);
  });

  it("generates monotonically distinct ids", () => {
    const s = new Store();
    const a = recordTransition(s, {
      kind: "slice",
      targetId: "slice-1",
      fromState: null,
      toState: "setup",
      at: "2026-05-20T00:00:00.000Z",
    });
    const b = recordTransition(s, {
      kind: "slice",
      targetId: "slice-1",
      fromState: "setup",
      toState: "work",
      at: "2026-05-20T00:00:01.000Z",
    });
    expect(a.id).not.toBe(b.id);
  });

  it("clearTransitions empties the buffer", () => {
    const s = new Store();
    recordTransition(s, {
      kind: "sprint",
      targetId: "sprint-1",
      fromState: "executing",
      toState: "merging",
      at: "2026-05-20T00:00:00.000Z",
    });
    expect(s.getState().transitions.length).toBe(1);
    clearTransitions(s);
    expect(s.getState().transitions.length).toBe(0);
  });
});
