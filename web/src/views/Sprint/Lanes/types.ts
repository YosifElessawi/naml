export type LaneStatus = "work" | "review" | "idle";

export type GanttBlockKind = "work" | "review" | "merged";

export interface GanttBlock {
  kind: GanttBlockKind;
  sliceId: string;
  title?: string;
  /** Position from left, 0-100. */
  leftPct: number;
  /** Width, 0-100. */
  widthPct: number;
}

export interface GanttGhost {
  sliceId: string;
  label?: string;
  leftPct: number;
  widthPct: number;
  /** Visually dimmer than regular ghost (e.g. speculative "likely next"). */
  speculative?: boolean;
}

export interface GanttReadyBand {
  leftPct: number;
  widthPct: number;
}

export interface LaneTrack {
  laneId: string;
  status: LaneStatus;
  blocks: GanttBlock[];
  ghosts: GanttGhost[];
  ready?: GanttReadyBand;
}

export interface LaneCard {
  laneId: string;
  status: LaneStatus;
  /** Slice currently in flight on this lane (omit for idle). */
  currentSliceId?: string;
  currentTitle?: string;
  /** Human duration string e.g. "1m48s". */
  duration?: string;
  /** Context window % consumed, 0-100. */
  contextPct?: number;
  /** Optional sub-label for idle pill, e.g. "2m14s". */
  idleFor?: string;
}

export type QueueChipKind = "blocked" | "ready";

export interface QueueChip {
  sliceId: string;
  kind: QueueChipKind;
  /** Free-form trailing text e.g. "blocked on slice-4" or "ready". */
  detail: string;
}

export interface LanesData {
  timeAxis: string[];
  nowPct: number;
  tracks: LaneTrack[];
  cards: LaneCard[];
  queue: QueueChip[];
  /** Right-aligned summary in the queue strip. */
  queueSummary: string;
}
