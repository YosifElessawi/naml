// State-aware HOLD / RESUME / FAIL / SKIP button cluster for the slice
// drawer. Drop-in replacement for the stubbed buttons slice-7 shipped —
// slice-7's drawer file just needs:
//
//   import { HoldResumeControls } from "./HoldResumeControls";
//   …
//   <HoldResumeControls sliceId={data.id} state={data.state} prUrl={data.prUrl} />
//
// Kept as its own file so the merge composition between slice-7 and
// slice-14 is purely additive (no edit-conflict on SliceDrawer.tsx).
// The RESUME button specifically (and the FAIL / SKIP gated to the
// held state) is the AC#4 affordance the slice-14 spec called for.

import type { ReactNode } from "react";

import { useIntervene } from "./useIntervene.ts";

// Mirrors the python ``HOLDABLE_STATES`` frozenset; keep in sync with
// ``naml.states.HOLDABLE_STATES``. Used to decide whether the HOLD
// button is rendered. The reviewer-flagged "PR is not held-able" rule
// is enforced on the server (409) AND mirrored here so the UI hides
// the affordance entirely rather than offering a button that 409s.
const HOLDABLE_STATES: ReadonlySet<string> = new Set(["setup", "work"]);

export interface HoldResumeControlsProps {
  sliceId: string;
  /** Current slice state — drives which buttons render. */
  state: string;
  /** PR URL, present once the lane has opened a PR. When set, "View PR"
   *  is rendered alongside the failure controls. */
  prUrl?: string;
  /** Optional class for the wrapping <div>; lets slice-7's drawer style
   *  the cluster without leaking layout into this component. */
  className?: string;
}

export function HoldResumeControls({
  sliceId,
  state,
  prUrl,
  className,
}: HoldResumeControlsProps): ReactNode {
  const { run, pending, lastResult, clear } = useIntervene(sliceId);

  const onHold = () => {
    void run("hold");
  };
  const onResume = () => {
    void run("resume");
  };
  const onMarkFailed = () => {
    void run("mark-failed");
  };
  const onSkip = () => {
    void run("skip");
  };
  const onOpenTerminal = () => {
    void run("open-terminal");
  };

  // Render rules per the slice-14 spec + slice-7 drawer mockup:
  //
  //   setup | work    → HOLD (RESUME hidden because there's nothing
  //                     to resume), MARK FAILED, SKIP, terminal locked
  //   held            → RESUME (new — only when held), MARK FAILED,
  //                     SKIP, terminal UNLOCKED
  //   pr              → MARK FAILED, terminal unlocked (slice-14
  //                     spec exception: pr is "at rest" per the lane)
  //   review · review_passed · merged · failure terminals → no
  //                     intervention beyond open-terminal
  const showHold = HOLDABLE_STATES.has(state);
  const showResume = state === "held";
  const showMarkFailed = state !== "merged" && state !== "abandoned";
  const showSkip = showHold || state === "held";
  const showOpenTerminal = !showHold; // unlocked iff not setup/work
  const terminalLocked = showHold; // mirror — for the disabled tooltip

  return (
    <div className={className ?? "naml-drawer-actions"}>
      {showHold && (
        <button type="button" onClick={onHold} disabled={pending}>
          HOLD
        </button>
      )}
      {showResume && (
        <button type="button" onClick={onResume} disabled={pending} data-action="resume">
          RESUME
        </button>
      )}
      {showMarkFailed && (
        <button type="button" onClick={onMarkFailed} disabled={pending}>
          MARK FAILED
        </button>
      )}
      {showSkip && (
        <button type="button" onClick={onSkip} disabled={pending}>
          SKIP
        </button>
      )}
      {showOpenTerminal && (
        <button
          type="button"
          onClick={onOpenTerminal}
          disabled={pending}
          title="cd into the slice worktree and resume the Claude session"
        >
          OPEN IN TERMINAL
        </button>
      )}
      {terminalLocked && (
        <button
          type="button"
          disabled
          title={`Slice is in ${state.toUpperCase()} · resuming the session in a terminal would interrupt naml's run. HOLD first.`}
        >
          🔒 OPEN IN TERMINAL
        </button>
      )}
      {prUrl && (
        <a href={prUrl} target="_blank" rel="noreferrer noopener">
          VIEW PR ↗
        </a>
      )}
      {lastResult && (
        <output className="naml-drawer-flash">
          {lastResult.ok ? interpretSuccess(lastResult.action, state) : lastResult.error}
          <button type="button" onClick={clear} aria-label="Dismiss notification">
            ×
          </button>
        </output>
      )}
    </div>
  );
}

function interpretSuccess(action: string, state: string): string {
  switch (action) {
    case "hold":
      // The lane flips state to ``held`` on its own, but the SSE update
      // races with this success callback. Until ``state === "held"`` is
      // pushed in via the store, show the in-flight copy.
      return state === "held"
        ? "Held · terminal unlocked"
        : "Hold requested · waiting for current turn";
    case "resume":
      return "Resume requested · lane re-engaging";
    case "mark-failed":
      return "Marked failed";
    case "skip":
      return "Skipped";
    case "open-terminal":
      return "Opening terminal…";
    default:
      return "Done";
  }
}
