// Wire shape for the SliceCard component.
//
// The card is pure-presentational: every value the UI displays comes in
// through `SliceCardData`. When the SSE pipeline (slice-11/12) emits per-slice
// telemetry it should produce this exact shape so the card is a drop-in.

export type SliceState =
  | "pending"
  | "setup"
  | "work"
  | "pr"
  | "review"
  | "merged"
  | "failed"
  | "needs_human_review"
  | "blocked_upstream"
  | "abandoned"
  | "held";

export type SliceKind = "AFK" | "HITL";

export type ReviewVerdict = "lgtm" | "request_changes" | "abandon" | "waiting";

export interface TokenCounts {
  input: number;
  output: number;
  cache: number;
}

export interface DurationMeter {
  elapsedSec: number;
  budgetSec: number | null;
  label?: string;
}

export interface ContextMeter {
  pct: number;
  used: number;
  cap: number;
  label?: string;
}

export interface TraversalStep {
  state: SliceState;
  status: "done" | "current" | "pending";
  retry?: { n: number; cap: number };
}

export interface ReviewDetail {
  verdict: ReviewVerdict;
  reviewerId?: string;
}

export interface SliceCardData {
  id: string;
  title: string;
  state: SliceState;
  kind: SliceKind;
  lane?: string | null;

  duration: DurationMeter;
  context: ContextMeter;

  costUsd: number;
  tokens: TokenCounts;
  retry?: { n: number; cap: number } | null;
  lastGate?: string | null;

  sessionId?: string | null;
  prNumber?: number | null;
  prMerged?: boolean;
  tier?: number | null;
  review?: ReviewDetail | null;

  traversal?: TraversalStep[];

  dependsOn?: string[];
  touches?: string[];
  blockedReason?: string | null;

  // shown only when state === "held"
  heldReason?: string | null;
}
