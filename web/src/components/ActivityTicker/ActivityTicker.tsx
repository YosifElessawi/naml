// Activity ticker — the right rail's heartbeat indicator.
//
// Consumes `store.transitions` (newest-first, capped by TRANSITIONS_CAP) and
// renders the most recent N entries. Each entry slides in via the global
// `naml-anim-ticker-entry` class (defined in styles/animations.css, 250ms
// slide-in from top, neutralized under prefers-reduced-motion).

import { useEffect, useState } from "react";

import { type Store, store as defaultStore } from "../../store/store.ts";
import type { Transition } from "../../store/types.ts";

interface ActivityTickerProps {
  store?: Store;
  /** Max entries to render. Defaults to 4 per spec ("3-4 most-recent"). */
  limit?: number;
  /** Override `Date.now` for deterministic age labels in tests. */
  now?: () => number;
}

function ageLabel(at: string, now: () => number): string {
  const ms = now() - new Date(at).getTime();
  const secs = Math.max(0, Math.round(ms / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

function describe(t: Transition): string {
  const arrow = t.fromState ? `${t.fromState} → ${t.toState}` : t.toState;
  return `${t.targetId}: ${arrow}`;
}

export function ActivityTicker({
  store = defaultStore,
  limit = 4,
  now = Date.now,
}: ActivityTickerProps) {
  const [transitions, setTransitions] = useState<Transition[]>(() => store.getState().transitions);

  useEffect(() => {
    const pull = () => setTransitions(store.getState().transitions);
    const off = store.subscribeKey("transitions", pull);
    pull();
    return off;
  }, [store]);

  const visible = transitions.slice(0, limit);

  return (
    <section className="naml-activity" aria-label="Activity" aria-live="polite" aria-atomic="false">
      <header className="naml-activity__header">Activity</header>
      {visible.length === 0 ? (
        <p className="naml-activity__empty">No state changes yet.</p>
      ) : (
        <ul className="naml-activity__list">
          {visible.map((t) => (
            <li
              key={t.id}
              className="naml-activity__entry naml-anim-ticker-entry"
              data-kind={t.kind}
              data-target={t.targetId}
            >
              <span className="naml-activity__what">{describe(t)}</span>
              <span className="naml-activity__when naml-anim-timestamp">{ageLabel(t.at, now)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
