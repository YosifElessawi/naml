export function formatDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "—";
  const total = Math.round(sec);
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}

export function formatBudgetTail(sec: number | null): string {
  if (sec == null) return "";
  return ` / ~${formatDuration(sec)} budget`;
}

export function formatUsd(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `$${n.toFixed(2)}`;
}

export function formatTokensShort(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(Math.round(n));
}

export function contextSeverity(pct: number): "ok" | "warn" | "danger" {
  if (pct >= 85) return "danger";
  if (pct >= 70) return "warn";
  return "ok";
}

export function durationSeverity(elapsedSec: number, budgetSec: number | null): "ok" | "warn" | "danger" {
  if (!budgetSec || budgetSec <= 0) return "ok";
  const ratio = elapsedSec / budgetSec;
  if (ratio >= 1) return "danger";
  if (ratio >= 0.7) return "warn";
  return "ok";
}

export function shortenSessionId(id: string | null | undefined): string {
  if (!id) return "—";
  if (id.length <= 9) return id;
  return `${id.slice(0, 4)}…${id.slice(-4)}`;
}

export function stateLabel(state: string): string {
  if (state === "needs_human_review") return "HITL";
  if (state === "blocked_upstream") return "BLOCKED";
  return state.toUpperCase();
}
