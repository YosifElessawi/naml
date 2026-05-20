// Live-store translator for `<Lanes />`. The slice-5 component was
// designed against a fixture that carried gantt-positioned blocks
// (leftPct/widthPct), but the live store doesn't track per-event
// timelines — it only knows the *current* state of each slice. So we
// produce a faithful subset: cards + queue + simple proportional
// gantt blocks based on per-lane slice count.
//
// Once we wire a per-slice "duration in state" series into the store,
// we can swap this for a true timeline view without touching the
// component.

import { useEffect, useState } from "react";

import { type Store, store as defaultStore } from "../../../store/store.ts";
import type {
  SliceState as DomainSliceState,
  NamlStore,
  SliceSummary,
  SprintSummary,
} from "../../../store/types.ts";
import type {
  GanttBlock,
  GanttBlockKind,
  LaneCard,
  LaneStatus,
  LaneTrack,
  LanesData,
  QueueChip,
} from "./types.ts";

const TIME_AXIS = ["start", "early", "mid", "late", "now", "eta"];
const NOW_PCT = 75;

function pickSprint(state: NamlStore): SprintSummary | null {
  const sprints = Object.values(state.sprints);
  if (sprints.length === 0) return null;
  return sprints.slice().sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))[0] ?? null;
}

function blockKindFor(state: DomainSliceState): GanttBlockKind {
  if (state === "merged") return "merged";
  if (state === "review" || state === "pr") return "review";
  return "work";
}

function laneStatusFor(active: SliceSummary | undefined): LaneStatus {
  if (!active) return "idle";
  if (active.state === "review" || active.state === "pr") return "review";
  if (active.state === "work" || active.state === "setup") return "work";
  return "idle";
}

function pickActiveSlice(group: SliceSummary[]): SliceSummary | undefined {
  return (
    group.find((s) => ["work", "pr", "review", "setup"].includes(s.state)) ??
    group.find((s) => s.state !== "merged") ??
    group[0]
  );
}

function laneIdFor(n: number): string {
  return `lane-${n + 1}`;
}

export function selectLanesLive(state: NamlStore): LanesData | null {
  const sprint = pickSprint(state);
  if (!sprint) return null;
  const sprintSlices = Object.values(state.slices).filter((s) => s.sprintId === sprint.id);
  if (sprintSlices.length === 0) return null;

  // Group slices by lane. Unassigned (lane === null) → queue.
  const byLane = new Map<number, SliceSummary[]>();
  const queueSlices: SliceSummary[] = [];
  for (const slice of sprintSlices) {
    if (slice.lane === null || slice.lane === undefined) {
      // Unassigned slices that aren't merged go into the queue strip.
      if (slice.state !== "merged") queueSlices.push(slice);
      continue;
    }
    const list = byLane.get(slice.lane) ?? [];
    list.push(slice);
    byLane.set(slice.lane, list);
  }

  const tracks: LaneTrack[] = [];
  const cards: LaneCard[] = [];

  const sortedLanes = [...byLane.entries()].sort(([a], [b]) => a - b);
  if (sortedLanes.length === 0) {
    // No lane assignments yet — synthesize a single lane-1 from the
    // unassigned slices so the user can still see them.
    sortedLanes.push([0, sprintSlices]);
  }

  for (const [lane, group] of sortedLanes) {
    const sorted = group
      .slice()
      .sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
    const active = pickActiveSlice(sorted);
    const status = laneStatusFor(active);
    const id = laneIdFor(lane);

    // Build proportional gantt blocks: each slice in the lane gets an
    // equal-width segment, coloured by its current state.
    const blocks: GanttBlock[] = sorted.map((s, i) => {
      const widthPct = 90 / sorted.length;
      return {
        kind: blockKindFor(s.state),
        sliceId: s.id.split("::").pop() ?? s.id,
        leftPct: i * widthPct,
        widthPct,
      };
    });

    tracks.push({ laneId: id, status, blocks, ghosts: [] });

    const ctxPct = active ? (state.slice_metrics[active.id]?.ctx_pct ?? 0) : 0;
    cards.push({
      laneId: id,
      status,
      currentSliceId:
        active && status !== "idle" ? (active.id.split("::").pop() ?? active.id) : undefined,
      currentTitle: active?.title || undefined,
      duration: undefined,
      contextPct: active ? ctxPct : undefined,
      idleFor: status === "idle" ? "ready" : undefined,
    });
  }

  const queue: QueueChip[] = queueSlices
    .sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
    .map((s) => ({
      sliceId: s.id.split("::").pop() ?? s.id,
      kind: s.state === "blocked_upstream" ? "blocked" : "ready",
      detail: s.state === "blocked_upstream" ? "blocked upstream" : "ready",
    }));

  const laneSummary = `${tracks.length} lane${tracks.length === 1 ? "" : "s"}`;
  const readyCount = queue.filter((q) => q.kind === "ready").length;
  const blockedCount = queue.filter((q) => q.kind === "blocked").length;
  const idleCount = cards.filter((c) => c.status === "idle").length;
  const queueSummary = `${laneSummary} · ${readyCount} ready · ${blockedCount} blocked · ${idleCount} idle`;

  return {
    timeAxis: TIME_AXIS,
    nowPct: NOW_PCT,
    tracks,
    cards,
    queue,
    queueSummary,
  };
}

export function useLanesLive(store: Store = defaultStore): LanesData | null {
  const [snap, setSnap] = useState<LanesData | null>(() => selectLanesLive(store.getState()));
  useEffect(() => {
    const pull = () => setSnap(selectLanesLive(store.getState()));
    const offs = [
      store.subscribeKey("sprints", pull),
      store.subscribeKey("slices", pull),
      store.subscribeKey("slice_metrics", pull),
    ];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store]);
  return snap;
}
