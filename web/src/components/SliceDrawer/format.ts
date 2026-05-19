import type { ContextMeter, SliceState } from "./types";

export const TERMINAL_LOCKED_STATES: ReadonlySet<SliceState> = new Set([
  "pending",
  "setup",
  "work",
  "pr",
]);

export const TERMINAL_UNLOCKED_STATES: ReadonlySet<SliceState> = new Set([
  "held",
  "review",
  "merged",
  "failed",
  "needs_human_review",
  "blocked_upstream",
  "abandoned",
]);

export function isTerminalLocked(state: SliceState): boolean {
  return !TERMINAL_UNLOCKED_STATES.has(state);
}

export function isRestingState(state: SliceState): boolean {
  return TERMINAL_UNLOCKED_STATES.has(state);
}

export function contextSeverity(meter: ContextMeter): "ok" | "amber" | "red" {
  const pct = (meter.used / meter.max) * 100;
  if (pct >= 85) return "red";
  if (pct >= 70) return "amber";
  return "ok";
}

export function formatPct(meter: ContextMeter): string {
  const pct = Math.round((meter.used / meter.max) * 100);
  return `${pct}%`;
}

export function stateLabel(state: SliceState): string {
  if (state === "needs_human_review") return "HITL";
  if (state === "blocked_upstream") return "BLOCKED";
  return state.toUpperCase();
}

export function formatTokensShort(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return `${n}`;
}

export function formatUsd(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

export function shortenSessionId(id: string): string {
  if (id.length <= 16) return id;
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}
