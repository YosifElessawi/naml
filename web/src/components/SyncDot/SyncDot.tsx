import { useEffect, useState } from "react";

import { type Store, store as defaultStore } from "../../store/store.ts";
import type { NamlStore, SyncStatus } from "../../store/types.ts";

interface SyncDotProps {
  /** Inject a store for tests / Storybook. Falls back to the singleton. */
  store?: Store;
  /** When true, render the 18px collapsed-strip variant (icon-only). */
  compact?: boolean;
  /** Override `Date.now` for deterministic age calculation in tests. */
  now?: () => number;
}

function ageLabel(lastEventAt: string | null, now: () => number): string {
  if (!lastEventAt) return "connecting…";
  const ms = now() - new Date(lastEventAt).getTime();
  const secs = Math.max(0, Math.round(ms / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

function tagline(
  status: SyncStatus,
  lastEventAt: string | null,
  attempt: number,
  now: () => number,
): string {
  switch (status) {
    case "live":
      return `Synced ${ageLabel(lastEventAt, now)}`;
    case "slow":
      return `Synced ${ageLabel(lastEventAt, now)}`;
    case "lost":
      return "Sync lost · retrying…";
    case "connecting":
      return `reconnecting attempt ${attempt || 1}/∞`;
  }
}

export function SyncDot({ store = defaultStore, compact = false, now = Date.now }: SyncDotProps) {
  const [snapshot, setSnapshot] = useState<{
    status: SyncStatus;
    lastEventAt: string | null;
    attempt: number;
  }>(() => {
    const s = store.getState();
    return {
      status: s.syncStatus,
      lastEventAt: s.lastEventAt,
      attempt: s.reconnectAttempt,
    };
  });

  useEffect(() => {
    const pull = () => {
      const s = store.getState();
      setSnapshot({
        status: s.syncStatus,
        lastEventAt: s.lastEventAt,
        attempt: s.reconnectAttempt,
      });
    };
    const offs = [
      store.subscribeKey("syncStatus", pull),
      store.subscribeKey("lastEventAt", pull),
      store.subscribeKey("reconnectAttempt", pull),
    ];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store]);

  const { status, lastEventAt, attempt } = snapshot;
  const label = tagline(status, lastEventAt, attempt, now);

  if (compact) {
    return <output className="naml-sync-dot" data-sync={status} aria-label={label} title={label} />;
  }

  return (
    <output className="naml-sync" data-sync={status}>
      <span className="naml-sync-dot" data-sync={status} aria-hidden="true" />
      <span className="naml-sync__label naml-anim-timestamp">{label}</span>
    </output>
  );
}

/** Pure selector — exposed for components that prefer to render their own
 *  presentation but want the same label semantics. */
export function syncTagline(state: NamlStore, now: () => number = Date.now): string {
  return tagline(state.syncStatus, state.lastEventAt, state.reconnectAttempt, now);
}
