export type TabId =
  | "project"
  | "lanes"
  | "gates"
  | "account"
  | "sync"
  | "health"
  | "advanced";

export const TAB_ORDER: readonly TabId[] = [
  "project",
  "lanes",
  "gates",
  "account",
  "sync",
  "health",
  "advanced",
] as const;

export type GateStatus = "ok" | "running" | "fail" | "idle";

export interface GateRow {
  name: string;
  argv: string;
  status: GateStatus;
  ranAt?: string;
}

export interface LabelsConfig {
  sprintPrefix: string;
  slicePrefix: string;
  lifecycle: string[];
}

export interface ProjectConfig {
  repoSlug: string;
  baseBranch: string;
  projectRoot: string;
  adrFolder: string;
  lifetimeSince: string;
  labels: LabelsConfig;
}

export interface LanesConfig {
  defaultLanes: number;
  dagWidthDetection: boolean;
  hardCap: number;
}

export interface AccountConfig {
  configDir: string;
  configDirOptions: string[];
  model: string;
  modelOptions: string[];
  contextWindow: number;
  sessionTokenLimit: number | null;
  weeklyTokenLimit: number | null;
  sessionResetAt: string | null;
  weeklyResetAt: string | null;
}

export interface SyncConfig {
  heartbeatSeconds: number;
  slowThresholdSeconds: number;
  lostThresholdSeconds: number;
  reduceMotion: boolean;
}

export interface AdvancedConfig {
  featureFlags: Record<string, boolean>;
  rawConfigToml: string;
}

export interface NamlSettings {
  project: ProjectConfig;
  lanes: LanesConfig;
  gates: GateRow[];
  account: AccountConfig;
  sync: SyncConfig;
  advanced: AdvancedConfig;
}

export interface FieldError {
  message: string;
}
