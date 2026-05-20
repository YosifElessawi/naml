// Notifications bell.
//
// Q2/Q3 spec: header bell that surfaces escalations and ship-ready
// celebrations. Filters `store.sprints` + `store.slices` for the four
// states the user cares about (failed / merge_blocked / needs_human_review
// / awaiting_signoff). The red-dot indicator shows when any escalation is
// active; the popover lists them.

import { useEffect, useMemo, useState } from "react";

import { type Store, store as defaultStore } from "../../store/store.ts";
import type { NamlStore, SliceState, SprintState } from "../../store/types.ts";

const ESCALATION_SLICE_STATES: ReadonlySet<SliceState> = new Set(["failed", "needs_human_review"]);
const ESCALATION_SPRINT_STATES: ReadonlySet<SprintState> = new Set([
  "failed",
  "merge_blocked",
  "awaiting_signoff",
]);

export interface EscalationItem {
  kind: "slice" | "sprint";
  id: string;
  title: string;
  state: SliceState | SprintState;
}

export function selectEscalations(state: NamlStore): EscalationItem[] {
  const out: EscalationItem[] = [];
  for (const sprint of Object.values(state.sprints)) {
    if (ESCALATION_SPRINT_STATES.has(sprint.state)) {
      out.push({ kind: "sprint", id: sprint.id, title: sprint.title, state: sprint.state });
    }
  }
  for (const slice of Object.values(state.slices)) {
    if (ESCALATION_SLICE_STATES.has(slice.state)) {
      out.push({ kind: "slice", id: slice.id, title: slice.title, state: slice.state });
    }
  }
  return out;
}

interface NotificationsBellProps {
  store?: Store;
}

export function NotificationsBell({ store = defaultStore }: NotificationsBellProps) {
  const [snapshot, setSnapshot] = useState(() => store.getState());
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const pull = () => setSnapshot(store.getState());
    const offs = [store.subscribeKey("sprints", pull), store.subscribeKey("slices", pull)];
    pull();
    return () => {
      for (const off of offs) off();
    };
  }, [store]);

  const escalations = useMemo(() => selectEscalations(snapshot), [snapshot]);
  const count = escalations.length;
  const label =
    count === 0
      ? "Notifications: nothing needs attention"
      : `Notifications: ${count} item${count === 1 ? "" : "s"} need attention`;

  return (
    <div className="naml-bell">
      <button
        type="button"
        className="naml-bell__btn"
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        data-has-escalations={count > 0 ? "true" : "false"}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true">🔔</span>
        {count > 0 && (
          <span className="naml-bell__dot" data-count={String(count)} aria-hidden="true" />
        )}
      </button>
      {open && (
        <dialog className="naml-bell__pop" aria-label="Notifications" open>
          {count === 0 ? (
            <p className="naml-bell__empty">No escalations.</p>
          ) : (
            <ul className="naml-bell__list">
              {escalations.map((e) => (
                <li key={`${e.kind}:${e.id}`} className="naml-bell__item" data-state={e.state}>
                  <span className="naml-bell__kind">{e.kind.toUpperCase()}</span>
                  <span className="naml-bell__title">{e.title || e.id}</span>
                  <span className="naml-bell__state">{e.state.toUpperCase()}</span>
                </li>
              ))}
            </ul>
          )}
        </dialog>
      )}
    </div>
  );
}
