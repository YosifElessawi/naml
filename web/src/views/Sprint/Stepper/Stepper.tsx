import styles from "./Stepper.module.css";
import { stepperFixture } from "./fixture.ts";
import {
  type AlternateExit,
  type SliceState,
  SPRINT_STATES,
  STATE_LABELS,
  type StepperData,
} from "./types.ts";

type StepStatus = "done" | "current" | "future";

function stepStatus(idx: number, currentIdx: number, allDone: boolean): StepStatus {
  if (allDone) return "done";
  if (idx < currentIdx) return "done";
  if (idx === currentIdx) return "current";
  return "future";
}

function stepGlyph(status: StepStatus, fallback: string): string {
  if (status === "done") return "✓";
  if (status === "current") return "▶";
  return fallback;
}

const cellClassByState: Record<SliceState, string> = {
  idle: styles.cellIdle,
  setup: styles.cellSetup,
  work: styles.cellWork,
  pr: styles.cellPr,
  review: styles.cellReview,
  merged: styles.cellMerged,
  failed: styles.cellFailed,
  blocked: styles.cellBlocked,
  human: styles.cellHuman,
};

function classes(...names: (string | false | undefined | null)[]): string {
  return names.filter(Boolean).join(" ");
}

export type StepperProps = {
  data?: StepperData;
};

export function Stepper({ data = stepperFixture }: StepperProps) {
  const currentIdx = SPRINT_STATES.indexOf(data.currentState);
  const allDone = data.currentState === "complete";

  return (
    <section className={styles.root} data-testid="sprint-stepper">
      <div className={styles.stepper} role="list" aria-label="Sprint state stepper">
        {SPRINT_STATES.map((state, idx) => {
          const status = stepStatus(idx, currentIdx, allDone);
          const isLast = idx === SPRINT_STATES.length - 1;
          return (
            <div
              key={state}
              role="listitem"
              data-state={state}
              data-status={status}
              className={classes(
                styles.step,
                status === "done" && styles.stepDone,
                status === "current" && styles.stepCurrent,
                isLast && styles.stepLast,
              )}
            >
              <div className={styles.dot} aria-hidden="true">
                {stepGlyph(status, String(idx))}
              </div>
              <div className={styles.lbl}>{STATE_LABELS[state]}</div>
              <div className={styles.dur}>{data.durations[state] ?? "—"}</div>
              {state === "executing" && data.sliceStates.length > 0 ? (
                <SubRail sliceStates={data.sliceStates} />
              ) : null}
            </div>
          );
        })}
      </div>

      <AuxStrip aux={data.aux} />
      <AlternateExits hit={data.hitExits} />
    </section>
  );
}

function SubRail({ sliceStates }: { sliceStates: SliceState[] }) {
  return (
    <div className={styles.subRail} aria-label="Slice progress sub-rail">
      {sliceStates.map((s, i) => (
        <span
          // biome-ignore lint/suspicious/noArrayIndexKey: cells are positional and uniform
          key={i}
          data-slice-state={s}
          className={classes(styles.cell, cellClassByState[s])}
        />
      ))}
    </div>
  );
}

function AuxStrip({ aux }: { aux: StepperData["aux"] }) {
  return (
    <div className={styles.aux} aria-label="Sprint metrics strip">
      <span className={styles.stat}>
        elapsed <b>{aux.elapsed}</b>
      </span>
      <span className={styles.divider} />
      <span className={classes(styles.stat, styles.statEta)}>
        ETA <b>{aux.eta}</b>
      </span>
      <span className={styles.divider} />
      <span className={styles.stat}>
        cost <b>{aux.cost}</b>
      </span>
      {aux.projTotal ? (
        <>
          <span className={styles.divider} />
          <span className={styles.stat}>
            proj total <b>{aux.projTotal}</b>
          </span>
        </>
      ) : null}
      <span className={styles.divider} />
      <span className={styles.stat}>
        lanes <b>{aux.lanes}</b>
      </span>
      <span className={styles.divider} />
      <span className={styles.stat}>
        slices <b>{aux.slices}</b>
      </span>
      {aux.retries ? (
        <>
          <span className={styles.divider} />
          <span className={styles.stat}>
            retries <b>{aux.retries}</b>
          </span>
        </>
      ) : null}
      {aux.tier1 ? (
        <>
          <span className={styles.divider} />
          <span className={styles.stat}>
            tier-1 <b>{aux.tier1}</b>
          </span>
        </>
      ) : null}
      {aux.lgtm ? (
        <>
          <span className={styles.divider} />
          <span className={styles.stat}>
            LGTM <b>{aux.lgtm}</b>
          </span>
        </>
      ) : null}
      <span className={styles.grow} />
      <span className={styles.lastTick}>▢ live · last tick {aux.lastTick}</span>
    </div>
  );
}

function AlternateExits({ hit }: { hit: AlternateExit[] }) {
  const isHit = (e: AlternateExit) => hit.includes(e);
  return (
    <div className={styles.fork} aria-label="Alternate exits">
      <span className={styles.forkLabel}>ALTERNATE EXITS</span>
      <span className={styles.exit}>
        <span
          data-exit="partial_failure"
          data-lit={isHit("partial_failure")}
          className={classes(
            styles.exitDot,
            styles.exitPartial,
            isHit("partial_failure") && styles.lit,
          )}
        />
        partial_failure
      </span>
      <span className={styles.arrow}>→</span>
      <span>awaiting_signoff</span>
      <span className={styles.exit}>
        <span
          data-exit="failed"
          data-lit={isHit("failed")}
          className={classes(styles.exitDot, styles.exitFailed, isHit("failed") && styles.lit)}
        />
        failed
      </span>
      <span className={styles.exit}>
        <span
          data-exit="merge_blocked"
          data-lit={isHit("merge_blocked")}
          className={classes(
            styles.exitDot,
            styles.exitHuman,
            isHit("merge_blocked") && styles.lit,
          )}
        />
        merge_blocked
      </span>
      <span className={styles.arrow}>→</span>
      <span>human</span>
    </div>
  );
}
