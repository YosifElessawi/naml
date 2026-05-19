import type { LanesData } from "./types.ts";

export const lanesFixture: LanesData = {
  timeAxis: ["14:02", "14:08", "14:14", "14:20", "now", "eta"],
  nowPct: 68,
  tracks: [
    {
      laneId: "lane-1",
      status: "review",
      blocks: [
        { kind: "merged", sliceId: "slice-1", leftPct: 0, widthPct: 28 },
        { kind: "review", sliceId: "slice-3", leftPct: 30, widthPct: 28 },
      ],
      ghosts: [{ sliceId: "slice-6", leftPct: 60, widthPct: 22 }],
    },
    {
      laneId: "lane-2",
      status: "work",
      blocks: [
        { kind: "merged", sliceId: "slice-0", leftPct: 2, widthPct: 24 },
        { kind: "work", sliceId: "slice-4 ▶", leftPct: 26, widthPct: 42 },
      ],
      ghosts: [{ sliceId: "slice-7", leftPct: 70, widthPct: 22 }],
    },
    {
      laneId: "lane-3",
      status: "idle",
      blocks: [{ kind: "merged", sliceId: "slice-2", leftPct: 0, widthPct: 50 }],
      ghosts: [],
      ready: { leftPct: 52, widthPct: 16 },
    },
  ],
  cards: [
    {
      laneId: "lane-1",
      status: "review",
      currentSliceId: "slice-3",
      currentTitle: "Render config schema validator",
      duration: "1m48s",
      contextPct: 41,
    },
    {
      laneId: "lane-2",
      status: "work",
      currentSliceId: "slice-4",
      currentTitle: "HTML renderer + mock-target snapshot",
      duration: "6m12s",
      contextPct: 73,
    },
    {
      laneId: "lane-3",
      status: "idle",
      idleFor: "2m14s",
    },
  ],
  queue: [
    { sliceId: "slice-5", kind: "blocked", detail: "blocked on slice-4" },
    { sliceId: "slice-6", kind: "ready", detail: "ready" },
    { sliceId: "slice-7", kind: "ready", detail: "ready" },
    { sliceId: "slice-8", kind: "blocked", detail: "blocked on slice-6" },
  ],
  queueSummary: "3 lanes · 2 ready · 2 blocked · 1 idle",
};
