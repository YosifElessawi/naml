// Fetcher for the Settings → Health page's 30-day trend graphs.
//
// Unlike the live cost timeline (driven by SSE metric-tick), the trend
// graphs are *daily* aggregates that don't change inside a day. We fetch
// the rollup file (`state/aggregates-history.jsonl`, served by the
// Python side as `/api/aggregates-history`) on mount and then refresh
// every few minutes — there's no SSE pressure on this surface.

import type { Store } from "./store.ts";
import type { AggregatesHistory, AggregatesHistoryPoint } from "./types.ts";

export const AGGREGATES_HISTORY_ENDPOINT = "/api/aggregates-history";
export const DEFAULT_REFRESH_MS = 5 * 60 * 1000;

interface FetchOptions {
  endpoint?: string;
  fetcher?: typeof fetch;
  refreshMs?: number;
  setInterval?: (cb: () => void, ms: number) => unknown;
  clearInterval?: (handle: unknown) => void;
  now?: () => number;
  days?: number;
}

export interface AggregatesHistoryResponse {
  points: AggregatesHistoryPoint[];
}

export interface AggregatesHistoryPoller {
  stop(): void;
  fetchOnce(): Promise<AggregatesHistory | null>;
}

export function startAggregatesHistoryPoll(
  store: Store,
  opts: FetchOptions = {},
): AggregatesHistoryPoller {
  const endpoint = opts.endpoint ?? AGGREGATES_HISTORY_ENDPOINT;
  const fetcher = opts.fetcher ?? (typeof fetch === "function" ? fetch : null);
  const refreshMs = opts.refreshMs ?? DEFAULT_REFRESH_MS;
  const schedule = opts.setInterval ?? ((cb, ms) => globalThis.setInterval(cb, ms) as unknown);
  const cancel =
    opts.clearInterval ??
    ((h) => globalThis.clearInterval(h as ReturnType<typeof globalThis.setInterval>));
  const now = opts.now ?? Date.now;
  const days = opts.days ?? 30;

  async function fetchOnce(): Promise<AggregatesHistory | null> {
    if (!fetcher) return null;
    try {
      const url = `${endpoint}?days=${encodeURIComponent(String(days))}`;
      const res = await fetcher(url, { headers: { Accept: "application/json" } });
      if (!res.ok) return null;
      const raw = (await res.json()) as AggregatesHistoryResponse;
      const points: AggregatesHistoryPoint[] = (raw.points ?? []).map((p) => ({
        day: String(p.day),
        cost_usd: Number(p.cost_usd ?? 0),
        tokens: Number(p.tokens ?? 0),
        sprints_started: Number(p.sprints_started ?? 0),
        sprints_completed: Number(p.sprints_completed ?? 0),
        tier1_hit_count: Number(p.tier1_hit_count ?? 0),
        slice_fails: Number(p.slice_fails ?? 0),
      }));
      const next: AggregatesHistory = {
        points,
        lastFetchedAt: new Date(now()).toISOString(),
      };
      store.patch("aggregates_history", next);
      return next;
    } catch {
      return null;
    }
  }

  void fetchOnce();
  const handle = schedule(() => {
    void fetchOnce();
  }, refreshMs);

  return {
    stop: () => cancel(handle),
    fetchOnce,
  };
}
