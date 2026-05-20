import type { SliceCardData, SliceState } from "./types";

// Fixtures cover every slice state. Used by snapshot tests in
// SliceCard.test.tsx and as Storybook-style references for downstream slices.

const baseFixture: SliceCardData = {
  id: "slice-4",
  title: "Add HTML renderer with mock-target snapshot",
  state: "work",
  kind: "AFK",
  lane: "lane-2",
  duration: { elapsedSec: 372, budgetSec: 540, label: "DURATION · IN STATE" },
  context: { pct: 73, used: 146_000, cap: 200_000, label: "CONTEXT WINDOW" },
  costUsd: 0.41,
  tokens: { input: 82_000, output: 14_000, cache: 1_200_000 },
  retry: { n: 1, cap: 2 },
  lastGate: "typecheck ▶",
  sessionId: "9b0c1234abcdef13",
  traversal: [
    { state: "setup", status: "done" },
    { state: "work", status: "current", retry: { n: 1, cap: 2 } },
    { state: "pr", status: "pending" },
    { state: "review", status: "pending" },
    { state: "merged", status: "pending" },
  ],
};

export const workingSliceFixture: SliceCardData = { ...baseFixture };

export const reviewSliceFixture: SliceCardData = {
  id: "slice-3",
  title: "Render config schema validator",
  state: "review",
  kind: "AFK",
  lane: "lane-1",
  duration: { elapsedSec: 108, budgetSec: 360 },
  context: { pct: 41, used: 82_000, cap: 200_000 },
  costUsd: 0.28,
  tokens: { input: 54_000, output: 9_000, cache: 800_000 },
  retry: null,
  prNumber: 142,
  prMerged: false,
  review: { verdict: "waiting", reviewerId: "A2-fresh" },
  sessionId: "8a1cdef2beef1234",
  traversal: [
    { state: "setup", status: "done" },
    { state: "work", status: "done" },
    { state: "pr", status: "done" },
    { state: "review", status: "current" },
    { state: "merged", status: "pending" },
  ],
};

export const mergedSliceFixture: SliceCardData = {
  id: "slice-2",
  title: "Config schema + golden tests",
  state: "merged",
  kind: "AFK",
  duration: { elapsedSec: 702, budgetSec: null, label: "TOTAL DURATION" },
  context: { pct: 58, used: 116_000, cap: 200_000, label: "PEAK CONTEXT" },
  costUsd: 0.74,
  tokens: { input: 140_000, output: 18_000, cache: 1_800_000 },
  retry: null,
  prNumber: 141,
  prMerged: true,
  tier: 1,
  sessionId: "7b2def3fbeef5678",
  traversal: [
    { state: "setup", status: "done" },
    { state: "work", status: "done" },
    { state: "pr", status: "done" },
    { state: "review", status: "done" },
    { state: "merged", status: "current" },
  ],
};

export const blockedSliceFixture: SliceCardData = {
  id: "slice-5",
  title: "Eval harness integration",
  state: "blocked_upstream",
  kind: "AFK",
  duration: { elapsedSec: 0, budgetSec: null },
  context: { pct: 0, used: 0, cap: 200_000 },
  costUsd: 0,
  tokens: { input: 0, output: 0, cache: 0 },
  dependsOn: ["slice-3", "slice-4"],
  touches: ["tests/eval/**"],
  blockedReason: "queued for next free lane",
};

export const pendingSliceFixture: SliceCardData = {
  id: "slice-6",
  title: "Wire up settings drawer",
  state: "pending",
  kind: "AFK",
  duration: { elapsedSec: 0, budgetSec: null },
  context: { pct: 0, used: 0, cap: 200_000 },
  costUsd: 0,
  tokens: { input: 0, output: 0, cache: 0 },
};

export const setupSliceFixture: SliceCardData = {
  id: "slice-7",
  title: "Cold-start replay benchmark",
  state: "setup",
  kind: "AFK",
  lane: "lane-3",
  duration: { elapsedSec: 22, budgetSec: 60 },
  context: { pct: 8, used: 16_000, cap: 200_000 },
  costUsd: 0.02,
  tokens: { input: 8_000, output: 800, cache: 12_000 },
  sessionId: "abc1234ef0",
};

export const prSliceFixture: SliceCardData = {
  id: "slice-8",
  title: "Push branch + open PR",
  state: "pr",
  kind: "AFK",
  lane: "lane-2",
  duration: { elapsedSec: 14, budgetSec: 90 },
  context: { pct: 22, used: 44_000, cap: 200_000 },
  costUsd: 0.05,
  tokens: { input: 18_000, output: 1_500, cache: 60_000 },
  prNumber: 144,
  sessionId: "f00ddeadbeef99",
};

export const failedSliceFixture: SliceCardData = {
  id: "slice-9",
  title: "Cost timeline rollup endpoint",
  state: "failed",
  kind: "AFK",
  lane: "lane-1",
  duration: { elapsedSec: 612, budgetSec: 540 },
  context: { pct: 89, used: 178_000, cap: 200_000 },
  costUsd: 1.12,
  tokens: { input: 220_000, output: 22_000, cache: 2_400_000 },
  retry: { n: 2, cap: 2 },
  lastGate: "pytest tests/aggregator/ ✗",
  sessionId: "bad0123ffff",
  traversal: [
    { state: "setup", status: "done" },
    { state: "work", status: "current", retry: { n: 2, cap: 2 } },
    { state: "failed", status: "current" },
  ],
};

export const needsHumanReviewSliceFixture: SliceCardData = {
  id: "slice-10",
  title: "Migrate schema → v2 (destructive)",
  state: "needs_human_review",
  kind: "HITL",
  duration: { elapsedSec: 1_240, budgetSec: 900 },
  context: { pct: 64, used: 128_000, cap: 200_000 },
  costUsd: 0.92,
  tokens: { input: 196_000, output: 12_000, cache: 1_600_000 },
  prNumber: 145,
  sessionId: "hum0deadbeef",
  lastGate: "manual:HITL-stop",
};

export const abandonedSliceFixture: SliceCardData = {
  id: "slice-11",
  title: "Replace global store with signals (deferred)",
  state: "abandoned",
  kind: "AFK",
  duration: { elapsedSec: 420, budgetSec: 540 },
  context: { pct: 51, used: 102_000, cap: 200_000 },
  costUsd: 0.31,
  tokens: { input: 76_000, output: 6_000, cache: 320_000 },
  retry: { n: 2, cap: 2 },
  review: { verdict: "abandon" },
  sessionId: "ab44deaddead",
};

export const heldSliceFixture: SliceCardData = {
  id: "slice-12",
  title: "Wire SSE heartbeats into right rail",
  state: "held",
  kind: "AFK",
  lane: "lane-2",
  duration: { elapsedSec: 198, budgetSec: 540 },
  context: { pct: 36, used: 72_000, cap: 200_000 },
  costUsd: 0.18,
  tokens: { input: 38_000, output: 4_000, cache: 240_000 },
  sessionId: "held00deadbeef",
  heldReason: "paused by user — terminal opened",
  traversal: [
    { state: "setup", status: "done" },
    { state: "work", status: "current" },
    { state: "held", status: "current" },
  ],
};

export const sliceCardFixturesByState: Record<SliceState, SliceCardData> = {
  pending: pendingSliceFixture,
  setup: setupSliceFixture,
  work: workingSliceFixture,
  pr: prSliceFixture,
  review: reviewSliceFixture,
  merged: mergedSliceFixture,
  failed: failedSliceFixture,
  needs_human_review: needsHumanReviewSliceFixture,
  blocked_upstream: blockedSliceFixture,
  abandoned: abandonedSliceFixture,
  held: heldSliceFixture,
};

export const sliceCardFixture: SliceCardData = workingSliceFixture;
