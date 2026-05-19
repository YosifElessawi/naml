export type SprintState =
  | "planning"
  | "publishing"
  | "executing"
  | "awaiting_signoff"
  | "merging"
  | "complete";

export type SliceState =
  | "idle"
  | "setup"
  | "work"
  | "pr"
  | "review"
  | "merged"
  | "failed"
  | "blocked"
  | "human";

export type AlternateExit = "partial_failure" | "failed" | "merge_blocked";

export type AuxStats = {
  elapsed: string;
  eta: string;
  cost: string;
  projTotal?: string;
  lanes: string;
  slices: string;
  retries?: string;
  tier1?: string;
  lgtm?: string;
  lastTick: string;
};

export type StepperData = {
  currentState: SprintState;
  durations: Partial<Record<SprintState, string>>;
  sliceStates: SliceState[];
  aux: AuxStats;
  hitExits: AlternateExit[];
};

export const SPRINT_STATES: readonly SprintState[] = [
  "planning",
  "publishing",
  "executing",
  "awaiting_signoff",
  "merging",
  "complete",
] as const;

export const STATE_LABELS: Record<SprintState, string> = {
  planning: "Planning",
  publishing: "Publishing",
  executing: "Executing",
  awaiting_signoff: "Awaiting sign-off",
  merging: "Merging",
  complete: "Complete",
};
