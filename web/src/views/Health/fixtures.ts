// Aggregator-backed fixtures for the Health view. Replaced by GET /aggregates
// (slice-10) + live wire-up (slice-13). Numbers are illustrative — same
// shape the real endpoint will return.

export interface TrendPoint {
  /** ISO date for daily series, ISO datetime for sub-day series. */
  t: string;
  v: number;
}

export interface TrendCard {
  id: string;
  label: string;
  value: string;
  detail: string;
  series: TrendPoint[];
  tone: "default" | "ok" | "warn";
  amberAt?: number;
}

export interface BarCard {
  id: string;
  label: string;
  value: string;
  detail: string;
  bars: Array<{ key: string; v: number; color: string }>;
}

export interface HealthFixture {
  trends: TrendCard[];
  bars: BarCard[];
  followups: Array<{ label: string; value: string }>;
}

function isoDay(daysAgo: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

function series(n: number, gen: (i: number) => number): TrendPoint[] {
  const out: TrendPoint[] = [];
  for (let i = n - 1; i >= 0; i--) {
    out.push({ t: isoDay(i), v: gen(i) });
  }
  return out;
}

export const FIXTURE_HEALTH: HealthFixture = {
  trends: [
    {
      id: "daily-cost",
      label: "Daily cost · 30d",
      value: "$118.42",
      detail: "avg $3.94/day · peak $11.20",
      tone: "default",
      series: series(
        30,
        (i) => 2.2 + Math.sin(i / 3) * 1.1 + (i % 7 === 0 ? 5 : 0) + Math.random() * 1.4,
      ),
    },
    {
      id: "tier1-hit",
      label: "Tier-1 hit · 30d",
      value: "78%",
      detail: "7d: 78% · 30d: 74% · ↗ +4pp",
      tone: "ok",
      series: series(30, (i) => 60 + (29 - i) * 0.7 + Math.sin(i / 5) * 4),
    },
    {
      id: "sprint-duration",
      label: "Avg sprint duration · 30d",
      value: "37m",
      detail: "p50 37m · p95 62m · ↘ faster",
      tone: "default",
      series: series(30, (i) => 50 - (29 - i) * 0.5 + Math.sin(i / 4) * 3),
    },
    {
      id: "slice-fail-rate",
      label: "Slice fail rate · 7d",
      value: "5.6%",
      detail: "↗ +1.2pp · check ctx exhaustion",
      tone: "warn",
      amberAt: 5,
      series: series(7, (i) => 3.2 + (6 - i) * 0.4 + Math.sin(i) * 0.6),
    },
    {
      id: "lgtm-first-pass",
      label: "Review · LGTM 1st pass",
      value: "82%",
      detail: "mean iters 1.2 · disagree 6%",
      tone: "ok",
      series: series(30, (i) => 70 + (29 - i) * 0.4 + Math.cos(i / 6) * 5),
    },
  ],
  bars: [
    {
      id: "per-skill-cost",
      label: "Per-skill cost · 30d",
      value: "implement $76",
      detail: "review $24 · merge $9 · grill $5",
      bars: [
        { key: "implement", v: 76, color: "var(--c-accent, #38bdf8)" },
        { key: "review", v: 24, color: "var(--c-pr, #a78bfa)" },
        { key: "merge", v: 9, color: "var(--c-accent-2, #34d399)" },
        { key: "grill", v: 5, color: "var(--c-warn, #fbbf24)" },
      ],
    },
  ],
  followups: [
    { label: "per-state retry breakdown", value: "setup 3% · work 11% · review 4%" },
    { label: "reviewer/implementer disagreement", value: "6% (last 30d)" },
    { label: "TTFP histogram", value: "p50 14s · p95 36s" },
  ],
};
