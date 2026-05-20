// Right-rail Cost Timeline block.
//
// Reads `store.project_metrics` (today / week / 30d / lifetime) and uses
// `lib/tween` to count every figure up smoothly when the underlying value
// changes. Lifetime is tinted cyan per the Q7b mockup.

import { useEffect, useRef, useState } from "react";

import { DEFAULT_TWEEN_MS, type TweenCancel, tween } from "../../lib/tween.ts";
import { type Store, store as defaultStore } from "../../store/store.ts";
import type { ProjectMetrics } from "../../store/types.ts";

const FIELDS = ["today_cost", "week_cost", "last_30d_cost", "lifetime_cost"] as const;
type Field = (typeof FIELDS)[number];

const LABELS: Record<Field, string> = {
  today_cost: "Today",
  week_cost: "This week",
  last_30d_cost: "Last 30 days",
  lifetime_cost: "Lifetime",
};

function formatUsd(value: number): string {
  // Three significant digits for small values, otherwise standard $X.YY.
  if (value < 0.01) return "$0.00";
  if (value < 10) return `$${value.toFixed(2)}`;
  if (value < 1000) return `$${value.toFixed(1)}`;
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

interface CostTimelineProps {
  store?: Store;
  /** Inject a tween implementation. Defaults to the canonical lib/tween. */
  tweenFn?: typeof tween;
  /** Duration override — kept here so the e2e test can run sync. */
  tweenMs?: number;
}

export function CostTimeline({
  store = defaultStore,
  tweenFn = tween,
  tweenMs = DEFAULT_TWEEN_MS,
}: CostTimelineProps) {
  const [metrics, setMetrics] = useState<ProjectMetrics>(() => store.getState().project_metrics);
  // Per-field displayed value (tweened) and a per-field cancel handle so
  // we never have two RAF chains racing on the same counter.
  const [displayed, setDisplayed] = useState<Record<Field, number>>(() => ({
    today_cost: metrics.today_cost,
    week_cost: metrics.week_cost,
    last_30d_cost: metrics.last_30d_cost,
    lifetime_cost: metrics.lifetime_cost,
  }));
  const cancels = useRef<Partial<Record<Field, TweenCancel>>>({});
  const displayedRef = useRef(displayed);
  displayedRef.current = displayed;

  useEffect(() => {
    const pull = () => setMetrics(store.getState().project_metrics);
    const off = store.subscribeKey("project_metrics", pull);
    pull();
    return off;
  }, [store]);

  useEffect(() => {
    for (const field of FIELDS) {
      const next = metrics[field];
      const prev = displayedRef.current[field];
      if (prev === next) continue;
      cancels.current[field]?.();
      cancels.current[field] = tweenFn(prev, next, tweenMs, (v) => {
        setDisplayed((d) => ({ ...d, [field]: v }));
      });
    }
  }, [metrics, tweenFn, tweenMs]);

  useEffect(() => {
    return () => {
      for (const c of Object.values(cancels.current)) c?.();
    };
  }, []);

  return (
    <section className="naml-cost" aria-label="Cost timeline">
      <header className="naml-cost__header">Cost Timeline</header>
      <ul className="naml-cost__grid">
        {FIELDS.map((field) => (
          <li
            key={field}
            className="naml-cost__cell"
            data-field={field}
            data-lifetime={field === "lifetime_cost" ? "true" : "false"}
          >
            <span className="naml-cost__label">{LABELS[field]}</span>
            <span className="naml-cost__value" data-testid={`cost-${field}`}>
              {formatUsd(displayed[field])}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
