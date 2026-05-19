import type { MouseEvent, RefObject } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import "./SliceDrawer.css";
import {
  contextSeverity,
  formatPct,
  formatTokensShort,
  formatUsd,
  isTerminalLocked,
  shortenSessionId,
  stateLabel,
} from "./format";
import type { InterveneResult, SliceDrawerData, SliceState } from "./types";

function lockTooltipFor(state: SliceState): string {
  return `Slice is in ${stateLabel(state)} · resuming the session in a terminal would interrupt naml's run. HOLD first.`;
}

export type InterveneAction = "hold" | "open-terminal" | "fail" | "skip";

export interface SliceDrawerProps {
  data: SliceDrawerData;
  onClose: () => void;
  /**
   * Action POSTer. Defaults to fetch-against `/intervene/<slice-id>?action=<action>`.
   * Override in tests so we don't hit the network.
   */
  onIntervene?: (sliceId: string, action: InterveneAction) => Promise<InterveneResult>;
}

const FLASH_MS = 2200;

async function defaultIntervene(
  sliceId: string,
  action: InterveneAction,
): Promise<InterveneResult> {
  const url = `/intervene/${encodeURIComponent(sliceId)}?action=${action}`;
  try {
    const res = await fetch(url, { method: "POST" });
    if (!res.ok) {
      return { ok: false, status: res.status, error: await res.text().catch(() => "") };
    }
    return { ok: true, status: res.status };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      error: err instanceof Error ? err.message : "network error",
    };
  }
}

export function SliceDrawer({ data, onClose, onIntervene }: SliceDrawerProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const [flash, setFlash] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!flash) return;
    const t = window.setTimeout(() => setFlash(null), FLASH_MS);
    return () => window.clearTimeout(t);
  }, [flash]);

  const handleOverlayClick = useCallback(
    (e: MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) {
        onClose();
      }
    },
    [onClose],
  );

  const intervene = onIntervene ?? defaultIntervene;

  const runAction = useCallback(
    async (action: InterveneAction, okText: string) => {
      const res = await intervene(data.id, action);
      if (res.ok) {
        setFlash({ kind: "ok", text: okText });
      } else {
        setFlash({
          kind: "error",
          text: `${action} failed${res.error ? `: ${res.error}` : ""}`,
        });
      }
    },
    [data.id, intervene],
  );

  const locked = isTerminalLocked(data.state);
  // HOLD is only meaningful while naml owns the session — already-resting
  // slices have nothing to pause.
  const canHold = locked;
  const canViewPr = Boolean(data.prUrl);

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: backdrop is mouse-only by design — keyboard escape (ESC) and the focusable × button cover keyboard users
    <div
      className="naml-drawer-overlay"
      onClick={handleOverlayClick}
      data-testid="slice-drawer-overlay"
    >
      <div
        ref={dialogRef}
        className="naml-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`naml-drawer-title-${data.id}`}
        data-testid="slice-drawer"
        data-state={data.state}
      >
        <Header
          data={data}
          onClose={onClose}
          closeRef={closeRef}
          titleId={`naml-drawer-title-${data.id}`}
        />

        <ActionBar
          data={data}
          locked={locked}
          canHold={canHold}
          canViewPr={canViewPr}
          onAction={runAction}
        />

        {locked ? <GateNote /> : null}

        {flash ? (
          <div
            className={`naml-drawer__flash${flash.kind === "error" ? " naml-drawer__flash--error" : ""}`}
            role="status"
            data-testid="slice-drawer-flash"
          >
            {flash.text}
          </div>
        ) : null}

        <div className="naml-drawer__body" data-testid="slice-drawer-body">
          <Overview data={data} />
          {data.acceptance && data.acceptance.length > 0 ? (
            <Acceptance items={data.acceptance} />
          ) : null}
          {data.traversal && data.traversal.length > 0 ? (
            <Timeline steps={data.traversal} />
          ) : null}
          {data.gateOutput ? <GateOutputBlock output={data.gateOutput} /> : null}
          <ReviewBlock data={data} />
          {data.notes ? <NotesBlock notes={data.notes} /> : null}
        </div>
      </div>
    </div>
  );
}

function Header({
  data,
  onClose,
  closeRef,
  titleId,
}: {
  data: SliceDrawerData;
  onClose: () => void;
  closeRef: RefObject<HTMLButtonElement>;
  titleId: string;
}) {
  return (
    <div className="naml-drawer__header">
      <div className="naml-drawer__header-grow">
        <span className="naml-drawer__sid">{data.id}</span>
        <h2 id={titleId} className="naml-drawer__title">
          {data.title}
        </h2>
        <div className="naml-drawer__meta">
          <StatePill state={data.state} />
          {data.afk ? (
            <span className="naml-drawer__pill naml-drawer__pill--afk">AFK</span>
          ) : null}
          {data.hitl ? (
            <span className="naml-drawer__pill naml-drawer__pill--hitl">HITL</span>
          ) : null}
          {data.lane ? (
            <span className="naml-drawer__pill naml-drawer__pill--lane">{data.lane}</span>
          ) : null}
          {data.startedAt || data.durationLabel ? (
            <>
              <span>·</span>
              <span>
                {data.startedAt ? `started ${data.startedAt}` : null}
                {data.startedAt && data.durationLabel ? " · " : null}
                {data.durationLabel ?? null}
              </span>
            </>
          ) : null}
          {data.sprintId ? (
            <>
              <span>·</span>
              <span>
                sprint{" "}
                <span style={{ color: "var(--text-0, #e7ecf3)" }}>{data.sprintId}</span>
              </span>
            </>
          ) : null}
        </div>
      </div>
      <button
        ref={closeRef}
        type="button"
        className="naml-drawer__close"
        aria-label="Close drawer"
        onClick={onClose}
      >
        ×
      </button>
    </div>
  );
}

function StatePill({ state }: { state: SliceState }) {
  const isLive = state === "work" || state === "review";
  return (
    <span
      className="naml-drawer__pill naml-drawer__pill--state"
      data-state={state}
      data-testid="slice-drawer-state-pill"
    >
      {isLive ? <span className="naml-drawer__pulse-dot" /> : null}
      {stateLabel(state)}
    </span>
  );
}

function ActionBar({
  data,
  locked,
  canHold,
  canViewPr,
  onAction,
}: {
  data: SliceDrawerData;
  locked: boolean;
  canHold: boolean;
  canViewPr: boolean;
  onAction: (action: InterveneAction, okText: string) => void;
}) {
  const copySessionId = useCallback(() => {
    if (!data.sessionId) return;
    navigator.clipboard?.writeText(data.sessionId).catch(() => {
      /* silent — clipboard permission/non-secure context */
    });
  }, [data.sessionId]);

  return (
    <div className="naml-drawer__actions" data-testid="slice-drawer-actions">
      <button
        type="button"
        className={`naml-drawer__actbtn ${locked ? "naml-drawer__actbtn--locked" : "naml-drawer__actbtn--primary"}`}
        title={locked ? lockTooltipFor(data.state) : "Open a Terminal at this slice's worktree"}
        aria-disabled={locked}
        disabled={locked}
        onClick={() => {
          if (locked) return;
          onAction("open-terminal", "Terminal launched");
        }}
        data-testid="open-in-terminal"
        data-locked={locked ? "true" : "false"}
      >
        ▶ OPEN IN TERMINAL
      </button>

      <button
        type="button"
        className="naml-drawer__actbtn naml-drawer__actbtn--ghost"
        onClick={copySessionId}
        disabled={!data.sessionId}
        data-testid="copy-session-id"
      >
        ⎘ copy session-id
      </button>

      <button
        type="button"
        className="naml-drawer__actbtn naml-drawer__actbtn--ghost"
        disabled={!data.branch}
        data-testid="checkout-branch"
      >
        ⎇ checkout branch
      </button>

      <button
        type="button"
        className={`naml-drawer__actbtn naml-drawer__actbtn--ghost${canViewPr ? "" : " naml-drawer__actbtn--locked"}`}
        disabled={!canViewPr}
        onClick={() => {
          if (!canViewPr || !data.prUrl) return;
          window.open(data.prUrl, "_blank", "noopener,noreferrer");
        }}
        data-testid="view-pr"
      >
        ↗ view PR{canViewPr ? "" : " (when ready)"}
      </button>

      <span className="naml-drawer__action-spacer" />

      <button
        type="button"
        className="naml-drawer__actbtn naml-drawer__actbtn--warn"
        onClick={() => onAction("hold", "Hold requested")}
        disabled={!canHold}
        data-testid="hold-button"
      >
        ⏸ HOLD &amp; UNLOCK TERMINAL
      </button>

      <button
        type="button"
        className="naml-drawer__actbtn naml-drawer__actbtn--danger"
        onClick={() => onAction("fail", "Mark-failed requested")}
        data-testid="mark-failed"
      >
        ✕ MARK FAILED
      </button>

      <button
        type="button"
        className="naml-drawer__actbtn naml-drawer__actbtn--ghost"
        onClick={() => onAction("skip", "Skip requested")}
        data-testid="skip-button"
      >
        ⏭ SKIP
      </button>
    </div>
  );
}

function GateNote() {
  return (
    <div className="naml-drawer__gate-note" role="note" data-testid="slice-drawer-gate-note">
      <span className="naml-drawer__gate-note-icon" aria-hidden="true">
        ⚠
      </span>
      <div>
        <strong>"Open in terminal" is locked while naml owns the session.</strong>{" "}
        Resuming this slice's Claude session from a separate terminal would yank the
        conversation out from under the lane worker and corrupt the run. The button unlocks
        automatically when the slice transitions to a resting state.
        <div className="naml-drawer__gate-path">
          LOCKED in: <code>setup · work · pr</code> &nbsp;·&nbsp; UNLOCKED in: <b>held</b> ·{" "}
          <b>review</b> · <b>merged</b> · <b>failed</b> · <b>needs_human_review</b> ·{" "}
          <b>abandoned</b>
        </div>
      </div>
    </div>
  );
}

function Overview({ data }: { data: SliceDrawerData }) {
  const ctxSev = data.context ? contextSeverity(data.context) : "ok";
  const retrySev =
    data.retry && data.retry.count > 0 ? (data.retry.count >= data.retry.max ? "red" : "amber") : "ok";
  return (
    <section
      className="naml-drawer__section"
      aria-label="Slice overview"
      data-testid="slice-drawer-overview"
    >
      <div className="naml-drawer__section-head">
        <span>OVERVIEW</span>
        <span className="naml-drawer__section-extra">all live data</span>
      </div>
      <div className="naml-drawer__section-body">
        <div className="naml-drawer__ogrid">
          <Cell k="SESSION ID" v={data.sessionId ? shortenSessionId(data.sessionId) : "—"} copy={data.sessionId} />
          <Cell k="BRANCH" v={data.branch ?? "—"} copy={data.branch} />
          <Cell k="WORKTREE" v={data.worktree ?? "—"} />
          <Cell k="DEPENDS ON" v={data.dependsOn ?? "—"} severity="ok" />
          <Cell k="TOUCHES" v={data.touches ?? "—"} />
          <Cell k="PR" v={data.prUrl ?? "not yet open"} severity={data.prUrl ? "ok" : undefined} />
          <Cell
            k="COST · TOKENS"
            v={
              data.cost !== undefined && data.tokens
                ? `${formatUsd(data.cost)} · ${formatTokensShort(data.tokens.input)} in / ${formatTokensShort(
                    data.tokens.output,
                  )} out / ${formatTokensShort(data.tokens.cache)} cache`
                : "—"
            }
          />
          <Cell
            k="CONTEXT WINDOW"
            v={
              data.context
                ? `${formatPct(data.context)} · ${formatTokensShort(data.context.used)} / ${formatTokensShort(
                    data.context.max,
                  )}`
                : "—"
            }
            severity={ctxSev}
          />
          <Cell
            k="RETRY · WORK STATE"
            v={data.retry ? `${data.retry.count} of ${data.retry.max}` : "—"}
            severity={retrySev}
          />
          <Cell
            k="DURATION · IN STATE"
            v={
              data.duration
                ? `${formatSec(data.duration.elapsedSec)}${data.duration.budgetSec ? ` · ~${formatSec(data.duration.budgetSec)} budget` : ""}`
                : "—"
            }
          />
        </div>
      </div>
    </section>
  );
}

function Cell({
  k,
  v,
  copy,
  severity,
}: {
  k: string;
  v: string;
  copy?: string;
  severity?: "ok" | "amber" | "red";
}) {
  const handleCopy = () => {
    if (!copy) return;
    navigator.clipboard?.writeText(copy).catch(() => {
      /* silent */
    });
  };
  return (
    <div>
      <div className="naml-drawer__ogrid-k">{k}</div>
      <div className="naml-drawer__ogrid-v" data-severity={severity}>
        <code>{v}</code>
        {copy ? (
          <button
            type="button"
            className="naml-drawer__copy"
            aria-label={`Copy ${k}`}
            onClick={handleCopy}
          >
            ⎘
          </button>
        ) : null}
      </div>
    </div>
  );
}

function formatSec(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

function Acceptance({ items }: { items: NonNullable<SliceDrawerData["acceptance"]> }) {
  const done = items.filter((i) => i.status === "done").length;
  return (
    <section
      className="naml-drawer__section"
      aria-label="Acceptance criteria"
      data-testid="slice-drawer-ac"
    >
      <div className="naml-drawer__section-head">
        <span>ACCEPTANCE CRITERIA</span>
        <span className="naml-drawer__section-extra">
          {done} of {items.length} verified
        </span>
      </div>
      <div className="naml-drawer__section-body">
        <div className="naml-drawer__ac">
          {items.map((it, i) => (
            <div
              key={`${i}-${it.status}-${it.text.slice(0, 16)}`}
              className="naml-drawer__ac-item"
              data-status={it.status}
            >
              <span className="naml-drawer__ac-box" aria-hidden="true">
                {it.status === "done" ? "✓" : it.status === "run" ? "▶" : ""}
              </span>
              <span className="naml-drawer__ac-text">
                {it.text}
                {it.note ? <span className="naml-drawer__ac-note">{it.note}</span> : null}
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Timeline({ steps }: { steps: NonNullable<SliceDrawerData["traversal"]> }) {
  return (
    <section
      className="naml-drawer__section"
      aria-label="State machine traversal"
      data-testid="slice-drawer-timeline"
    >
      <div className="naml-drawer__section-head">
        <span>STATE MACHINE · TRAVERSAL</span>
        <span className="naml-drawer__section-extra">live</span>
      </div>
      <div className="naml-drawer__section-body">
        <div className="naml-drawer__timeline">
          <div className="naml-drawer__timeline-spine" />
          {steps.map((step, i) => (
            <div
              key={`${i}-${step.state}-${step.status}`}
              className="naml-drawer__tevent"
              data-status={step.status}
            >
              <div className="naml-drawer__tevent-head">
                <span className="naml-drawer__tevent-name">
                  {typeof step.state === "string" ? step.state.toUpperCase() : String(step.state)}
                </span>
                {step.timestamp ? (
                  <span className="naml-drawer__tevent-ts">{step.timestamp}</span>
                ) : null}
                {step.duration ? (
                  <span className="naml-drawer__tevent-dur">{step.duration}</span>
                ) : null}
              </div>
              {step.detail ? (
                <div className="naml-drawer__tevent-detail">
                  {step.gateFail ? (
                    <span className="naml-drawer__gate-fail-badge">GATE FAIL</span>
                  ) : null}
                  {step.detail}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function GateOutputBlock({ output }: { output: NonNullable<SliceDrawerData["gateOutput"]> }) {
  return (
    <section
      className="naml-drawer__section"
      aria-label="Latest gate output"
      data-testid="slice-drawer-gate-output"
    >
      <div className="naml-drawer__section-head">
        <span>LATEST GATE OUTPUT</span>
        <span className="naml-drawer__section-extra">
          {output.command}
          {output.failedAt ? ` · ${output.failedAt}` : ""}
        </span>
      </div>
      <div className="naml-drawer__section-body">
        <pre className="naml-drawer__gate-out">{output.body}</pre>
      </div>
    </section>
  );
}

function ReviewBlock({ data }: { data: SliceDrawerData }) {
  const verdict = data.review?.verdict ?? "pending";
  const reached = ["review", "merged"].includes(data.state);
  const body = reached
    ? (data.review?.body ?? "")
    : "Auto-reviewer (A2-fresh) will spawn when this slice reaches the pr state. It reviews against the acceptance criteria above, the slice spec, and the parent ADRs.";
  return (
    <section
      className="naml-drawer__section"
      aria-label="Auto-review"
      data-testid="slice-drawer-review"
    >
      <div className="naml-drawer__section-head">
        <span>AUTO-REVIEW</span>
        <span className="naml-drawer__section-extra">
          {reached ? "verdict" : "pending · slice not yet at PR"}
        </span>
      </div>
      <div className="naml-drawer__section-body">
        <div className="naml-drawer__verdict" data-verdict={verdict}>
          <div className="naml-drawer__verdict-head">
            <span>VERDICT</span>
            <span className="naml-drawer__verdict-badge" data-verdict={verdict}>
              {verdict === "lgtm"
                ? "LGTM"
                : verdict === "request_changes"
                  ? "REQUEST_CHANGES"
                  : "PENDING"}
            </span>
          </div>
          <div className="naml-drawer__verdict-body">{body}</div>
        </div>
      </div>
    </section>
  );
}

function NotesBlock({ notes }: { notes: string }) {
  return (
    <section
      className="naml-drawer__section"
      aria-label="Notes from grilling session"
      data-testid="slice-drawer-notes"
    >
      <div className="naml-drawer__section-head">
        <span>NOTES FROM GRILLING SESSION</span>
      </div>
      <div className="naml-drawer__section-body">
        <div className="naml-drawer__notes">{notes}</div>
      </div>
    </section>
  );
}
