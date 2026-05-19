import type { MutableRefObject, ReactNode } from "react";
import { useEffect, useRef } from "react";
import "./SliceCard.css";
import { CounterTween } from "./CounterTween";
import {
  contextSeverity,
  durationSeverity,
  formatBudgetTail,
  formatDuration,
  formatTokensShort,
  formatUsd,
  shortenSessionId,
  stateLabel,
} from "./format";
import type { SliceCardData, SliceState, TraversalStep } from "./types";

export interface SliceCardProps {
  data: SliceCardData;
  variant?: "full" | "compact";
}

const PULSING_STATES: ReadonlySet<SliceState> = new Set<SliceState>([
  "setup",
  "work",
  "pr",
  "review",
]);

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function stateModifier(state: SliceState): string {
  return state.replace(/_/g, "-");
}

export function SliceCard({ data, variant = "full" }: SliceCardProps) {
  const cardRef = useRef<HTMLDivElement | null>(null);
  const prevStateRef = useRef<SliceState>(data.state);

  // Card-flip animation on state change (FLIP — First, Last, Invert, Play).
  // We can't actually move the card here (it lives wherever the parent puts it),
  // so the "flip" is a rotateX flash that signals the transition. ~300ms.
  useEffect(() => {
    if (prevStateRef.current === data.state) return;
    prevStateRef.current = data.state;
    const node = cardRef.current;
    if (!node) return;
    if (prefersReducedMotion()) {
      return;
    }
    node.classList.remove("naml-scard--flip");
    // force reflow so the class re-add restarts the animation
    void node.offsetWidth;
    node.classList.add("naml-scard--flip");
  }, [data.state]);

  if (variant === "compact") {
    return <CompactCard data={data} cardRef={cardRef} />;
  }
  return <FullCard data={data} cardRef={cardRef} />;
}

interface InnerProps {
  data: SliceCardData;
  cardRef: MutableRefObject<HTMLDivElement | null>;
}

function FullCard({ data, cardRef }: InnerProps) {
  const stateMod = stateModifier(data.state);
  const ctxSev = contextSeverity(data.context.pct);
  const durSev = durationSeverity(data.duration.elapsedSec, data.duration.budgetSec);
  const pulses = PULSING_STATES.has(data.state);

  return (
    <div
      ref={cardRef}
      className={`naml-scard naml-scard--full naml-scard--${stateMod}`}
      data-state={data.state}
      data-variant="full"
      role="article"
      aria-label={`slice ${data.id}: ${data.title} — ${stateLabel(data.state)}`}
    >
      <div className="naml-scard__row1">
        <span className="naml-scard__sid">{data.id}</span>
        <span className="naml-scard__title">{data.title}</span>
        <span className={`naml-scard__bdg naml-scard__bdg--${data.kind.toLowerCase()}`}>
          {data.kind}
        </span>
        {data.lane ? (
          <span className="naml-scard__bdg naml-scard__bdg--lane">{data.lane}</span>
        ) : null}
        <span className={`naml-scard__pill naml-scard__pill--${stateMod}`}>
          <span className={`naml-scard__pill-dot${pulses ? " naml-scard__pulse" : ""}`} />
          {stateLabel(data.state)}
        </span>
      </div>

      <div className="naml-scard__meters">
        <Meter
          label={data.duration.label ?? "DURATION · IN STATE"}
          value={
            <>
              {formatDuration(data.duration.elapsedSec)}
              {data.duration.budgetSec != null && (
                <span className="naml-scard__meter-budget">
                  {formatBudgetTail(data.duration.budgetSec)}
                </span>
              )}
            </>
          }
          valueSeverity={durSev}
          fillPct={
            data.duration.budgetSec
              ? Math.min(100, (data.duration.elapsedSec / data.duration.budgetSec) * 100)
              : 100
          }
          fillSeverity={durSev}
          fillVariant="work"
          budgetMarkerPct={data.duration.budgetSec ? 88 : null}
        />
        <Meter
          label={data.context.label ?? "CONTEXT WINDOW"}
          value={
            <>
              <CounterTween
                value={data.context.pct}
                format={(n) => `${Math.round(n)}%`}
              />
              <span className="naml-scard__meter-budget">
                {` · ${formatTokensShort(data.context.used)} / ${formatTokensShort(data.context.cap)}`}
              </span>
            </>
          }
          valueSeverity={ctxSev}
          fillPct={Math.min(100, data.context.pct)}
          fillSeverity={ctxSev}
          fillVariant="ctx"
        />
      </div>

      <div className="naml-scard__stats">
        <StatItem label="COST" value={formatUsd(data.costUsd)} />
        <Sep />
        <StatItem
          label="TOKENS"
          value={`${formatTokensShort(data.tokens.input)} in · ${formatTokensShort(
            data.tokens.output,
          )} out · ${formatTokensShort(data.tokens.cache)} cache`}
        />
        {data.retry && data.retry.n > 0 ? (
          <>
            <Sep />
            <StatItem
              label="RETRY"
              value={`${data.retry.n} / ${data.retry.cap}`}
              valueSeverity="warn"
            />
          </>
        ) : null}
        {data.lastGate ? (
          <>
            <Sep />
            <StatItem label="GATE" value={data.lastGate} />
          </>
        ) : null}
        {data.prNumber != null ? (
          <>
            <Sep />
            <StatItem
              label="PR"
              value={`#${data.prNumber}${data.prMerged ? " merged ✓" : ""}`}
              valueClassName="naml-scard__stat-pr"
            />
          </>
        ) : null}
        {data.tier != null ? (
          <>
            <Sep />
            <StatItem label="TIER" value={String(data.tier)} />
          </>
        ) : null}
        <span className="naml-scard__grow" />
        {data.sessionId ? (
          <span className="naml-scard__stat">
            <span className="naml-scard__stat-label">SESSION</span>
            <code className="naml-scard__session">{shortenSessionId(data.sessionId)}</code>
          </span>
        ) : null}
        <span className="naml-scard__actions">
          <button
            type="button"
            className="naml-scard__icon"
            aria-label="copy session id"
            title="copy session id"
          >
            ⎘
          </button>
          <button
            type="button"
            className="naml-scard__icon"
            aria-label="open in terminal"
            title="open in terminal"
          >
            ▶
          </button>
          <button
            type="button"
            className="naml-scard__icon"
            aria-label="view branch"
            title="view branch"
          >
            ⎇
          </button>
        </span>
      </div>

      {data.state === "held" ? (
        <div className="naml-scard__held-banner" role="status">
          <span className="naml-scard__held-icon" aria-hidden="true">
            ⏸
          </span>
          <span className="naml-scard__held-text">
            {data.heldReason ? data.heldReason : "paused by user"}
          </span>
        </div>
      ) : null}

      {data.state === "blocked_upstream" && data.dependsOn?.length ? (
        <div className="naml-scard__blocked-banner">
          <span className="naml-scard__stat-label">DEPENDS</span>
          <code>{data.dependsOn.join(", ")}</code>
          {data.touches?.length ? (
            <>
              <Sep />
              <span className="naml-scard__stat-label">TOUCHES</span>
              <code>{data.touches.join(", ")}</code>
            </>
          ) : null}
          {data.blockedReason ? (
            <span className="naml-scard__blocked-reason">{data.blockedReason}</span>
          ) : null}
        </div>
      ) : null}

      {data.state === "review" && data.review ? (
        <div className="naml-scard__inline-detail">
          <span className="naml-scard__stat-label">
            {data.review.verdict === "waiting" ? "VERDICT WAITING" : "VERDICT"}
          </span>
          <span
            className={`naml-scard__verdict naml-scard__verdict--${data.review.verdict}`}
          >
            {data.review.verdict === "waiting"
              ? "—"
              : data.review.verdict.toUpperCase().replace("_", " ")}
          </span>
          {data.review.reviewerId ? (
            <span className="naml-scard__reviewer">{data.review.reviewerId}</span>
          ) : null}
        </div>
      ) : null}

      {data.traversal?.length ? <Traversal steps={data.traversal} /> : null}
    </div>
  );
}

function CompactCard({ data, cardRef }: InnerProps) {
  const stateMod = stateModifier(data.state);
  const ctxSev = contextSeverity(data.context.pct);
  const pulses = PULSING_STATES.has(data.state);

  return (
    <div
      ref={cardRef}
      className={`naml-scard naml-scard--compact naml-scard--${stateMod}`}
      data-state={data.state}
      data-variant="compact"
      role="article"
      aria-label={`slice ${data.id}: ${data.title} — ${stateLabel(data.state)}`}
    >
      <div className="naml-scard__row1">
        <span className="naml-scard__sid">{data.id}</span>
        <span className="naml-scard__title">{data.title}</span>
        <span className={`naml-scard__pill naml-scard__pill--${stateMod}`}>
          <span className={`naml-scard__pill-dot${pulses ? " naml-scard__pulse" : ""}`} />
          {stateLabel(data.state)}
        </span>
      </div>
      <div className="naml-scard__meters naml-scard__meters--compact">
        <CompactMeter
          label="DUR"
          value={formatDuration(data.duration.elapsedSec)}
          fillPct={
            data.duration.budgetSec
              ? Math.min(100, (data.duration.elapsedSec / data.duration.budgetSec) * 100)
              : 100
          }
          severity={durationSeverity(data.duration.elapsedSec, data.duration.budgetSec)}
          fillVariant="work"
        />
        <CompactMeter
          label="CTX"
          value={`${Math.round(data.context.pct)}%`}
          fillPct={Math.min(100, data.context.pct)}
          severity={ctxSev}
          fillVariant="ctx"
        />
      </div>
      <div className="naml-scard__stats naml-scard__stats--compact">
        <StatItem label="COST" value={formatUsd(data.costUsd)} />
        {data.retry && data.retry.n > 0 ? (
          <>
            <Sep />
            <StatItem
              label="RETRY"
              value={`${data.retry.n} / ${data.retry.cap}`}
              valueSeverity="warn"
            />
          </>
        ) : null}
      </div>
    </div>
  );
}

interface MeterProps {
  label: string;
  value: ReactNode;
  valueSeverity?: "ok" | "warn" | "danger";
  fillPct: number;
  fillSeverity?: "ok" | "warn" | "danger";
  fillVariant?: "work" | "ctx";
  budgetMarkerPct?: number | null;
}

function Meter({
  label,
  value,
  valueSeverity = "ok",
  fillPct,
  fillSeverity = "ok",
  fillVariant = "work",
  budgetMarkerPct = null,
}: MeterProps) {
  return (
    <div className="naml-scard__meter">
      <div className="naml-scard__meter-head">
        <span className="naml-scard__meter-label">{label}</span>
        <span
          className={`naml-scard__meter-value naml-scard__meter-value--${valueSeverity}`}
        >
          {value}
        </span>
      </div>
      <div className="naml-scard__mbar">
        <div
          className={`naml-scard__mbar-fill naml-scard__mbar-fill--${fillVariant} naml-scard__mbar-fill--${fillSeverity}`}
          style={{ width: `${Math.max(0, Math.min(100, fillPct))}%` }}
        />
        {budgetMarkerPct != null ? (
          <div className="naml-scard__mbar-budget" style={{ left: `${budgetMarkerPct}%` }} />
        ) : null}
      </div>
    </div>
  );
}

interface CompactMeterProps {
  label: string;
  value: string;
  fillPct: number;
  severity: "ok" | "warn" | "danger";
  fillVariant: "work" | "ctx";
}

function CompactMeter({ label, value, fillPct, severity, fillVariant }: CompactMeterProps) {
  return (
    <div className="naml-scard__meter naml-scard__meter--compact">
      <div className="naml-scard__meter-head">
        <span className="naml-scard__meter-label">{label}</span>
        <span className={`naml-scard__meter-value naml-scard__meter-value--${severity}`}>
          {value}
        </span>
      </div>
      <div className="naml-scard__mbar">
        <div
          className={`naml-scard__mbar-fill naml-scard__mbar-fill--${fillVariant} naml-scard__mbar-fill--${severity}`}
          style={{ width: `${Math.max(0, Math.min(100, fillPct))}%` }}
        />
      </div>
    </div>
  );
}

interface StatItemProps {
  label: string;
  value: ReactNode;
  valueSeverity?: "ok" | "warn" | "danger";
  valueClassName?: string;
}

function StatItem({ label, value, valueSeverity = "ok", valueClassName }: StatItemProps) {
  return (
    <span className="naml-scard__stat">
      <span className="naml-scard__stat-label">{label}</span>
      <span
        className={[
          "naml-scard__stat-value",
          `naml-scard__stat-value--${valueSeverity}`,
          valueClassName ?? "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {value}
      </span>
    </span>
  );
}

function Sep() {
  return <span className="naml-scard__sep">·</span>;
}

interface TraversalProps {
  steps: TraversalStep[];
}

function Traversal({ steps }: TraversalProps) {
  return (
    <div className="naml-scard__traverse">
      {steps.map((step, i) => {
        const cls =
          step.status === "done"
            ? "naml-scard__t-step naml-scard__t-step--done"
            : step.status === "current"
              ? "naml-scard__t-step naml-scard__t-step--curr"
              : "naml-scard__t-step";
        const sep = i < steps.length - 1 ? <span className="naml-scard__t-arr">→</span> : null;
        return (
          <span key={`${step.state}-${i}`} className="naml-scard__t-wrap">
            <span className={cls}>
              {step.state.replace(/_/g, " ")}
              {step.retry && step.retry.n > 0 ? (
                <span className="naml-scard__t-retry">
                  retry {step.retry.n}/{step.retry.cap}
                </span>
              ) : null}
            </span>
            {sep}
          </span>
        );
      })}
    </div>
  );
}
