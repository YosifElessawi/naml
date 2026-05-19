import { useMemo, useState } from "react";
import css from "./Health.module.css";
import { FIXTURE_HEALTH, type BarCard, type TrendCard, type TrendPoint } from "./fixtures.ts";

const VIEW_W = 220;
const VIEW_H = 48;
const PAD_X = 4;
const PAD_Y = 6;

interface PointXY {
  x: number;
  y: number;
  point: TrendPoint;
}

function projectSeries(series: TrendPoint[]): PointXY[] {
  if (series.length === 0) {
    return [];
  }
  const ys = series.map((p) => p.v);
  let min = Math.min(...ys);
  let max = Math.max(...ys);
  if (min === max) {
    // Flat lines should sit centered, not stretched to the full height.
    min -= 1;
    max += 1;
  }
  const xStep = (VIEW_W - PAD_X * 2) / Math.max(1, series.length - 1);
  return series.map((point, i) => {
    const x = PAD_X + i * xStep;
    const norm = (point.v - min) / (max - min);
    const y = VIEW_H - PAD_Y - norm * (VIEW_H - PAD_Y * 2);
    return { x, y, point };
  });
}

function pointsAttr(pts: PointXY[]): string {
  return pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
}

function classes(...c: Array<string | undefined | false>): string {
  return c.filter(Boolean).join(" ");
}

function TrendChart({ card }: { card: TrendCard }) {
  const points = useMemo(() => projectSeries(card.series), [card.series]);
  const [hover, setHover] = useState<number | null>(null);
  const stroke = classes(
    css.chartLine,
    card.tone === "ok" && css.ok,
    card.tone === "warn" && css.warn,
  );
  const dotCls = classes(
    css.chartDot,
    card.tone === "ok" && css.ok,
    card.tone === "warn" && css.warn,
  );
  const valueCls = classes(
    css.cardValue,
    card.tone === "ok" && css.ok,
    card.tone === "warn" && css.warn,
  );

  // Amber band — rendered for series where amberAt is set (e.g. fail rate)
  const amberBand =
    card.amberAt != null && points.length > 0
      ? (() => {
          const ys = card.series.map((p) => p.v);
          const min = Math.min(...ys);
          const max = Math.max(...ys);
          if (min === max) return null;
          const norm = (card.amberAt - min) / (max - min);
          const yThresh = VIEW_H - PAD_Y - norm * (VIEW_H - PAD_Y * 2);
          if (yThresh < PAD_Y || yThresh > VIEW_H - PAD_Y) return null;
          return (
            <rect
              className={css.chartAmberBand}
              x={PAD_X}
              y={PAD_Y}
              width={VIEW_W - PAD_X * 2}
              height={yThresh - PAD_Y}
            />
          );
        })()
      : null;

  const hovered = hover != null ? points[hover] : null;

  return (
    <article className={css.card} data-card-id={card.id}>
      <span className={css.cardLabel}>{card.label}</span>
      <span className={valueCls}>{card.value}</span>
      <span className={css.cardDetail}>{card.detail}</span>
      <div className={css.chartWrap}>
        <svg
          role="img"
          aria-label={`${card.label} trend`}
          className={css.chart}
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          preserveAspectRatio="none"
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const x = ((e.clientX - rect.left) / rect.width) * VIEW_W;
            let nearest = 0;
            let bestDx = Infinity;
            points.forEach((p, i) => {
              const dx = Math.abs(p.x - x);
              if (dx < bestDx) {
                bestDx = dx;
                nearest = i;
              }
            });
            setHover(nearest);
          }}
        >
          <line
            className={css.chartAxis}
            x1={PAD_X}
            x2={VIEW_W - PAD_X}
            y1={VIEW_H - PAD_Y}
            y2={VIEW_H - PAD_Y}
          />
          {amberBand}
          <polyline className={stroke} points={pointsAttr(points)} />
          {hovered ? (
            <circle className={dotCls} cx={hovered.x} cy={hovered.y} r={3.2} />
          ) : null}
        </svg>
        {hovered ? (
          <div
            className={css.chartHover}
            style={{
              left: `${(hovered.x / VIEW_W) * 100}%`,
              top: `${(hovered.y / VIEW_H) * 100}%`,
            }}
          >
            {hovered.point.t} · {formatValue(hovered.point.v, card.id)}
          </div>
        ) : null}
      </div>
      <div className={css.legend}>
        <span>
          <span
            className={css.legendDot}
            style={{ background: legendColor(card.tone) }}
          />
          {card.label.split("·")[0]?.trim() ?? card.label}
        </span>
      </div>
    </article>
  );
}

function legendColor(tone: TrendCard["tone"]): string {
  if (tone === "ok") return "var(--c-accent-2, #34d399)";
  if (tone === "warn") return "var(--c-warn, #fbbf24)";
  return "var(--c-accent, #38bdf8)";
}

function formatValue(v: number, id: string): string {
  if (id === "daily-cost") return `$${v.toFixed(2)}`;
  if (id === "tier1-hit" || id === "slice-fail-rate" || id === "lgtm-first-pass") {
    return `${v.toFixed(1)}%`;
  }
  if (id === "sprint-duration") return `${v.toFixed(0)}m`;
  return v.toFixed(2);
}

function BarChart({ card }: { card: BarCard }) {
  const max = Math.max(...card.bars.map((b) => b.v));
  const barWidth = (VIEW_W - PAD_X * 2 - (card.bars.length - 1) * 4) / card.bars.length;
  return (
    <article className={css.card} data-card-id={card.id}>
      <span className={css.cardLabel}>{card.label}</span>
      <span className={css.cardValue}>{card.value}</span>
      <span className={css.cardDetail}>{card.detail}</span>
      <svg
        role="img"
        aria-label={`${card.label} by category`}
        className={css.barChart}
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="none"
      >
        {card.bars.map((bar, i) => {
          const h = ((bar.v / max) * (VIEW_H - PAD_Y * 2)) | 0;
          const y = VIEW_H - PAD_Y - h;
          const x = PAD_X + i * (barWidth + 4);
          return (
            <rect
              key={bar.key}
              x={x}
              y={y}
              width={barWidth}
              height={h}
              fill={bar.color}
              fillOpacity={0.7}
            />
          );
        })}
      </svg>
      <div className={css.legend}>
        {card.bars.map((bar) => (
          <span key={bar.key}>
            <span className={css.legendDot} style={{ background: bar.color }} />
            {bar.key}
          </span>
        ))}
      </div>
    </article>
  );
}

export function Health({
  data = FIXTURE_HEALTH,
}: {
  data?: typeof FIXTURE_HEALTH;
}) {
  return (
    <div className={css.root}>
      <section className={css.headerPanel}>
        <div className={css.headerBand}>▴ Health · trends</div>
        <div className={css.grid}>
          {data.trends.map((card) => (
            <TrendChart key={card.id} card={card} />
          ))}
          {data.bars.map((card) => (
            <BarChart key={card.id} card={card} />
          ))}
        </div>
        <div className={css.followups}>
          {data.followups.map((item) => (
            <span key={item.label} className={css.followupItem}>
              <span className="l">{item.label}:</span>
              <span>{item.value}</span>
            </span>
          ))}
        </div>
      </section>
    </div>
  );
}
