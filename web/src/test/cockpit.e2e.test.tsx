// End-to-end cockpit smoke test — drives a synthetic SSE stream through
// SseClient and snapshots the cockpit UI at 5 points along the way.
//
// Why not Playwright? The acceptance criterion is "end-to-end test runs a
// fixture sprint and snapshots the cockpit UI at 5 points." This file is
// the slice-13 component-graph end-to-end: SseClient → Store →
// (ActivityTicker + NotificationsBell + CostTimeline + SliceQueue +
// SyncDot). When slices 2-8 merge in, the same store hooks feed the real
// Dashboard / Sprint views — at which point this test can be lifted into
// a browser e2e harness without changing its event-driving shape.

import { act, render, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ActivityTicker } from "../components/ActivityTicker/index.ts";
import { CostTimeline } from "../components/CostTimeline/index.ts";
import { NotificationsBell } from "../components/NotificationsBell/index.ts";
import { SliceQueue } from "../components/SliceQueue/index.ts";
import { SyncDot } from "../components/SyncDot/index.ts";
import { Flip } from "../lib/flip.ts";
import { Store } from "../store/store.ts";
import { SseClient } from "../sync/EventSource.ts";

interface FakeES {
  listeners: Map<string, (ev: MessageEvent) => void>;
  emit(event: string, data: unknown): void;
  close: () => void;
  closed: boolean;
  onerror: ((ev: Event) => void) | null;
  onopen: ((ev: Event) => void) | null;
}

function makeFake(): FakeES {
  const listeners = new Map<string, (ev: MessageEvent) => void>();
  return {
    listeners,
    closed: false,
    onerror: null,
    onopen: null,
    emit(event, data) {
      const cb = listeners.get(event);
      if (!cb) return;
      cb({ data: JSON.stringify(data) } as MessageEvent);
    },
    close() {
      this.closed = true;
    },
  };
}

/** Sync tween — keeps the test deterministic by skipping the RAF dance.
 * Matches slice-12's tween signature: (from, to, onUpdate, options?). */
const syncTween: typeof import("../lib/tween.ts").tween = (_from, to, onUpdate) => {
  onUpdate(to);
  return () => {};
};

function Cockpit({ store, flip }: { store: Store; flip: Flip }) {
  return (
    <div className="naml-cockpit-e2e">
      <header data-testid="header">
        <SyncDot store={store} now={() => Date.parse("2026-05-20T00:00:30Z")} />
        <NotificationsBell store={store} />
      </header>
      <aside data-testid="rail">
        <CostTimeline store={store} tweenFn={syncTween} tweenMs={0} />
        <ActivityTicker store={store} now={() => Date.parse("2026-05-20T00:00:30Z")} />
      </aside>
      <main data-testid="queue">
        <SliceQueue store={store} flip={flip} />
      </main>
    </div>
  );
}

/** A minimal DOM fingerprint we can assert against at each step. */
interface Fingerprint {
  sync: string;
  notifications: string;
  costToday: string;
  costLifetime: string;
  activity: string[];
  queueIds: string[];
  queueFirstState: string | null;
}

function fingerprint(root: HTMLElement): Fingerprint {
  const sync = root.querySelector(".naml-sync")?.getAttribute("data-sync") ?? "";
  const notifications = root.querySelector(".naml-bell__btn")?.getAttribute("aria-label") ?? "";
  const costToday = root.querySelector('[data-testid="cost-today_cost"]')?.textContent ?? "";
  const costLifetime = root.querySelector('[data-testid="cost-lifetime_cost"]')?.textContent ?? "";
  const activityNodes = root.querySelectorAll(".naml-activity__entry");
  const activity = Array.from(activityNodes).map((n) => n.textContent ?? "");
  const queueNodes = root.querySelectorAll("[data-slice-id]");
  const queueIds = Array.from(queueNodes).map((n) => n.getAttribute("data-slice-id") ?? "");
  const first = queueNodes[0] ?? null;
  const queueFirstState = first ? first.getAttribute("data-state") : null;
  return { sync, notifications, costToday, costLifetime, activity, queueIds, queueFirstState };
}

describe("cockpit e2e — 5 fixture-sprint snapshot points", () => {
  it("walks a fixture sprint from connect → snapshot → state changes → escalation → recovery", () => {
    const store = new Store();
    const fakeEs = makeFake();
    const client = new SseClient(store, {
      url: "/events",
      factory: () => ({
        addEventListener: (type, cb) => fakeEs.listeners.set(type, cb),
        close: () => fakeEs.close(),
        get onerror() {
          return fakeEs.onerror;
        },
        set onerror(v) {
          fakeEs.onerror = v;
        },
        get onopen() {
          return fakeEs.onopen;
        },
        set onopen(v) {
          fakeEs.onopen = v;
        },
      }),
      now: () => Date.parse("2026-05-20T00:00:30Z"),
      setInterval: () => 1,
      clearInterval: () => {},
    });
    client.start();

    const flip = new Flip((el) => el.dataset.sliceId);
    flip.reducedMotionOverride = true; // deterministic in jsdom
    const { container, baseElement } = render(<Cockpit store={store} flip={flip} />);
    const root = within(baseElement).getByTestId("header").parentElement as HTMLElement;

    const snapshots: Fingerprint[] = [];
    const snap = () => snapshots.push(fingerprint(root));

    // ── Point 1: just after `start()` — connecting, empty cockpit ─────
    snap();

    // ── Point 2: server sends the snapshot event ──────────────────────
    act(() => {
      fakeEs.emit("snapshot", {
        sprints: {
          "sprint-1": {
            id: "sprint-1",
            title: "Cockpit",
            state: "executing",
            slicesDone: 1,
            slicesTotal: 3,
          },
        },
        slices: {
          "slice-3": {
            id: "slice-3",
            sprintId: "sprint-1",
            title: "Dashboard",
            state: "work",
            lane: 1,
          },
          "slice-4": {
            id: "slice-4",
            sprintId: "sprint-1",
            title: "Stepper",
            state: "work",
            lane: 2,
          },
        },
        aggregates: {
          per_project: {
            today_cost: 4.21,
            week_cost: 24.8,
            last_30d_cost: 118.42,
            lifetime_cost: 342.16,
          },
        },
      });
    });
    snap();

    // ── Point 3: state-update — slice-3 advances work → review ────────
    act(() => {
      fakeEs.emit("state-update", {
        kind: "slice",
        id: "slice-3",
        delta: { state: "review" },
      });
    });
    snap();

    // ── Point 4: escalation — slice-4 fails; metric-tick raises cost ──
    act(() => {
      fakeEs.emit("state-update", {
        kind: "slice",
        id: "slice-4",
        delta: { state: "failed" },
      });
      fakeEs.emit("metric-tick", {
        slice: "slice-4",
        sprint: "sprint-1",
        rollups: {
          slice_cost: 0.99,
          project_today: 5.2,
          project_lifetime: 343.14,
        },
      });
    });
    snap();

    // ── Point 5: recovery — slice-4 transitions back to review ────────
    act(() => {
      fakeEs.emit("state-update", {
        kind: "slice",
        id: "slice-4",
        delta: { state: "review" },
      });
    });
    snap();

    client.stop();
    container.remove();

    expect(snapshots).toHaveLength(5);

    // Point 1: connecting, nothing in the cockpit yet.
    expect(snapshots[0]?.sync).toBe("connecting");
    expect(snapshots[0]?.queueIds).toEqual([]);
    expect(snapshots[0]?.activity).toEqual([]);
    expect(snapshots[0]?.notifications).toMatch(/nothing needs attention/i);
    expect(snapshots[0]?.costToday).toBe("$0.00");

    // Point 2: snapshot landed → sync=live, cost cluster populated,
    // queue lists both slices (work before work, alpha tiebreak).
    expect(snapshots[1]?.sync).toBe("live");
    expect(snapshots[1]?.costToday).toBe("$4.21");
    expect(snapshots[1]?.costLifetime).toBe("$342.2");
    expect(snapshots[1]?.queueIds).toEqual(["slice-3", "slice-4"]);
    // No transition recorded yet (snapshot ≠ delta).
    expect(snapshots[1]?.activity).toEqual([]);

    // Point 3: slice-3 work → review. Activity ticker prepends the line.
    expect(snapshots[2]?.activity[0]).toMatch(/slice-3.*work.*review/i);
    // Review (4) sorts before work (6), so slice-3 jumps to the front.
    expect(snapshots[2]?.queueIds).toEqual(["slice-3", "slice-4"]);
    expect(snapshots[2]?.queueFirstState).toBe("review");

    // Point 4: slice-4 failed → escalation bell flips on; cost rolls up.
    expect(snapshots[3]?.notifications).toMatch(/1 item/i);
    expect(snapshots[3]?.costToday).toBe("$5.20");
    expect(snapshots[3]?.costLifetime).toBe("$343.1");
    // failed (1) sorts before review (4), so slice-4 is now at the front.
    expect(snapshots[3]?.queueIds).toEqual(["slice-4", "slice-3"]);
    expect(snapshots[3]?.queueFirstState).toBe("failed");
    // Ticker accumulates: most-recent (slice-4 failed) first, then slice-3.
    expect(snapshots[3]?.activity[0]).toMatch(/slice-4.*failed/i);

    // Point 5: recovery — escalation cleared, queue reorders again.
    expect(snapshots[4]?.notifications).toMatch(/nothing needs attention/i);
    expect(snapshots[4]?.queueIds).toEqual(["slice-3", "slice-4"]);
    expect(snapshots[4]?.activity[0]).toMatch(/slice-4.*failed.*review/i);
    expect(snapshots[4]?.queueFirstState).toBe("review");
  });
});
