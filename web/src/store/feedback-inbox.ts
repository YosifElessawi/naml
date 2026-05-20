// Feedback inbox poller.
//
// docs/feedback/inbox.md is editable on disk and is NOT pushed over SSE
// (per slice-13 spec: "slow cadence — 30s; not SSE-driven"). The browser
// fetches `/api/feedback-inbox` periodically; the Python side parses the
// markdown bullets and returns a deduped count + the 3 most recent.

import type { Store } from "./store.ts";
import type { FeedbackBullet, FeedbackInbox } from "./types.ts";

export const DEFAULT_POLL_MS = 30_000;
export const FEEDBACK_ENDPOINT = "/api/feedback-inbox";

interface PollOptions {
  intervalMs?: number;
  endpoint?: string;
  fetcher?: typeof fetch;
  /** Schedule poll. Defaults to setInterval; injected for tests. */
  setInterval?: (cb: () => void, ms: number) => unknown;
  clearInterval?: (handle: unknown) => void;
  now?: () => number;
}

export interface FeedbackInboxResponse {
  unfiledCount: number;
  bullets: Array<{
    id: string;
    text: string;
    source: string;
    addedAt?: string | null;
  }>;
}

export interface FeedbackInboxPoller {
  stop(): void;
  /** Manually trigger a fetch. Returns the resulting inbox or null on error. */
  pollOnce(): Promise<FeedbackInbox | null>;
}

export function startFeedbackInboxPoll(store: Store, opts: PollOptions = {}): FeedbackInboxPoller {
  const intervalMs = opts.intervalMs ?? DEFAULT_POLL_MS;
  const endpoint = opts.endpoint ?? FEEDBACK_ENDPOINT;
  const fetcher = opts.fetcher ?? (typeof fetch === "function" ? fetch : null);
  const schedule = opts.setInterval ?? ((cb, ms) => globalThis.setInterval(cb, ms) as unknown);
  const cancel =
    opts.clearInterval ??
    ((h) => globalThis.clearInterval(h as ReturnType<typeof globalThis.setInterval>));
  const now = opts.now ?? Date.now;

  async function pollOnce(): Promise<FeedbackInbox | null> {
    if (!fetcher) return null;
    try {
      const res = await fetcher(endpoint, { headers: { Accept: "application/json" } });
      if (!res.ok) return null;
      const raw = (await res.json()) as FeedbackInboxResponse;
      const bullets: FeedbackBullet[] = (raw.bullets ?? []).map((b) => ({
        id: b.id,
        text: b.text,
        source: b.source,
        addedAt: b.addedAt ?? null,
      }));
      const inbox: FeedbackInbox = {
        unfiledCount: raw.unfiledCount ?? bullets.length,
        bullets,
        lastPolledAt: new Date(now()).toISOString(),
      };
      store.patch("feedback_inbox", inbox);
      return inbox;
    } catch {
      return null;
    }
  }

  // Kick a poll immediately so the UI lights up on mount.
  void pollOnce();
  const handle = schedule(() => {
    void pollOnce();
  }, intervalMs);

  return {
    stop: () => cancel(handle),
    pollOnce,
  };
}
