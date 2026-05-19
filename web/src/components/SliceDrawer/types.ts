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

export type ReviewVerdict = "lgtm" | "request_changes" | "pending";

export interface TokenCounts {
  input: number;
  output: number;
  cache: number;
}

export interface ContextMeter {
  used: number;
  max: number;
}

export interface DurationMeter {
  elapsedSec: number;
  budgetSec: number | null;
}

export type TraversalStatus = "done" | "current" | "retry" | "pending";

export interface TraversalStep {
  state: SliceState | "WORK · RETRY 1/2" | string;
  status: TraversalStatus;
  timestamp?: string;
  duration?: string;
  detail?: string;
  gateFail?: boolean;
}

export interface AcceptanceItem {
  text: string;
  status: "done" | "run" | "pending";
  note?: string;
}

export interface ReviewDetail {
  verdict: ReviewVerdict;
  body: string;
}

export interface GateOutput {
  command: string;
  body: string;
  failedAt?: string;
}

export interface SliceDrawerData {
  id: string;
  title: string;
  state: SliceState;
  lane?: string;
  afk?: boolean;
  hitl?: boolean;
  startedAt?: string;
  durationLabel?: string;
  sprintId?: string;

  sessionId?: string;
  branch?: string;
  worktree?: string;
  dependsOn?: string;
  touches?: string;
  prUrl?: string | null;
  cost?: number;
  tokens?: TokenCounts;
  context?: ContextMeter;
  retry?: { count: number; max: number };
  duration?: DurationMeter;

  acceptance?: AcceptanceItem[];
  traversal?: TraversalStep[];
  gateOutput?: GateOutput | null;
  review?: ReviewDetail | null;
  notes?: string;
  heldReason?: string;
}

export interface InterveneResult {
  ok: boolean;
  status: number;
  error?: string;
}
