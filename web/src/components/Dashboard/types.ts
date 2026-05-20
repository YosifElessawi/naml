export type SliceStateColor =
  | "work"
  | "pr"
  | "review"
  | "merged"
  | "failed"
  | "idle"
  | "blocked"
  | "human";

export type SprintGroup = "active" | "queued" | "recent";
export type SprintPillState = "executing" | "planned" | "complete" | "signoff" | "failed";

export interface SprintRowData {
  id: string;
  title: string;
  titleSuffix?: string;
  subtitle: string;
  state: SprintPillState;
  stateLabel: string;
  slices: SliceStateColor[];
  metaTopRight?: string;
  metaBottomRight?: string;
  openLabel: string;
}

export interface CostSlice {
  dollars: number;
  tokens?: string;
  delta?: string;
  deltaDirection?: "up" | "down";
}

export interface ProjectMeta {
  name: string;
  state: string;
  branch: string;
  lanes: number;
  sprintsShipped: number;
  lastMergeMinutesAgo: number;
  dailyBurn7dAvgDollars: number;
}

export interface CostBlock {
  today: CostSlice;
  thisWeek: CostSlice;
  last30d: CostSlice;
  lifetime: { dollars: number; since: string };
}

export interface HealthStat {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "danger";
  alert?: boolean;
  trend?: string;
  trendTone?: "up" | "dn";
}

export interface InboxBullet {
  text: string;
  source: string;
}

export interface InboxBlock {
  unfiled: number;
  link: string;
  bullets: InboxBullet[];
}

export interface DashboardData {
  project: ProjectMeta;
  cost: CostBlock;
  health: HealthStat[];
  healthTertiary: HealthStat[];
  sprints: Record<SprintGroup, SprintRowData[]>;
  inbox: InboxBlock;
}
