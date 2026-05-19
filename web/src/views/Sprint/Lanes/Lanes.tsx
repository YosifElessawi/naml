import type {
  GanttBlock,
  GanttGhost,
  LaneCard,
  LaneTrack,
  LanesData,
  QueueChip,
} from "./types.ts";
import { lanesFixture } from "./fixture.ts";
import "./Lanes.css";

export interface LanesProps {
  data?: LanesData;
}

const pct = (v: number) => `${v}%`;

function GanttBlockEl({ block }: { block: GanttBlock }) {
  return (
    <div
      className={`naml-lanes__block naml-lanes__block--${block.kind}`}
      style={{ left: pct(block.leftPct), width: pct(block.widthPct) }}
    >
      <span className="naml-lanes__block-id">{block.sliceId}</span>
      {block.title ? <span className="naml-lanes__block-title">{block.title}</span> : null}
    </div>
  );
}

function GhostEl({ ghost }: { ghost: GanttGhost }) {
  const cls = ghost.speculative
    ? "naml-lanes__ghost naml-lanes__ghost--speculative"
    : "naml-lanes__ghost";
  return (
    <div
      className={cls}
      style={{ left: pct(ghost.leftPct), width: pct(ghost.widthPct) }}
    >
      <span className="naml-lanes__ghost-id">{ghost.sliceId}</span>
      {ghost.label ? <span>{ghost.label}</span> : null}
    </div>
  );
}

function LaneRow({ track, nowPct }: { track: LaneTrack; nowPct: number }) {
  const trackCls =
    track.status === "idle"
      ? "naml-lanes__track naml-lanes__track--idle"
      : "naml-lanes__track";
  return (
    <div className="naml-lanes__lane-row" data-lane={track.laneId}>
      <div className="naml-lanes__lane-label">{track.laneId}</div>
      <div className={trackCls} data-status={track.status}>
        {track.blocks.map((b) => (
          <GanttBlockEl key={`${track.laneId}-${b.sliceId}`} block={b} />
        ))}
        {track.ready ? (
          <div
            className="naml-lanes__ready"
            data-testid={`ready-${track.laneId}`}
            style={{ left: pct(track.ready.leftPct), width: pct(track.ready.widthPct) }}
          >
            ⌁ READY
          </div>
        ) : null}
        {track.ghosts.map((g) => (
          <GhostEl key={`${track.laneId}-${g.sliceId}-ghost`} ghost={g} />
        ))}
        <div
          className="naml-lanes__now-line"
          data-testid={`now-line-${track.laneId}`}
          style={{ left: pct(nowPct) }}
        />
      </div>
    </div>
  );
}

function pillLabel(status: LaneCard["status"], idleFor?: string): string {
  if (status === "work") return "WORK";
  if (status === "review") return "REVIEW";
  return idleFor ? `IDLE · ${idleFor}` : "IDLE";
}

function Card({ card }: { card: LaneCard }) {
  const cls = `naml-lanes__card naml-lanes__card--${card.status}`;
  const pillCls = `naml-lanes__pill naml-lanes__pill--${card.status}`;
  const ctxWarn = (card.contextPct ?? 0) >= 70;
  return (
    <div className={cls} data-lane={card.laneId}>
      <div className="naml-lanes__card-head">
        <span className="naml-lanes__card-name">{card.laneId}</span>
        <span className={pillCls}>
          <span className="naml-lanes__pill-dot" />
          {pillLabel(card.status, card.idleFor)}
        </span>
      </div>
      <div className="naml-lanes__card-body">
        {card.status === "idle" ? (
          <div className="naml-lanes__idle-msg">⌁ READY · NEXT SLICE WILL LAND HERE</div>
        ) : (
          <>
            <div className="naml-lanes__card-title">
              {card.currentSliceId ? (
                <span className="naml-lanes__card-title-sid">{card.currentSliceId}</span>
              ) : null}
              {card.currentSliceId && card.currentTitle ? " · " : null}
              {card.currentTitle}
            </div>
            <div className="naml-lanes__meters">
              <div>
                <div className="naml-lanes__meter-label">DUR</div>
                <div className="naml-lanes__meter-value">{card.duration ?? "—"}</div>
                <div className="naml-lanes__meter-bar">
                  <div className="naml-lanes__meter-bar-fill naml-lanes__meter-bar-fill--work" />
                </div>
              </div>
              <div>
                <div className="naml-lanes__meter-label">CTX</div>
                <div
                  className={
                    ctxWarn
                      ? "naml-lanes__meter-value naml-lanes__meter-value--warn"
                      : "naml-lanes__meter-value"
                  }
                >
                  {card.contextPct !== undefined ? `${card.contextPct}%` : "—"}
                </div>
                <div className="naml-lanes__meter-bar">
                  <div
                    className={
                      ctxWarn
                        ? "naml-lanes__meter-bar-fill naml-lanes__meter-bar-fill--warn"
                        : "naml-lanes__meter-bar-fill naml-lanes__meter-bar-fill--work"
                    }
                    style={{ width: pct(card.contextPct ?? 0) }}
                  />
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ChipEl({ chip }: { chip: QueueChip }) {
  const cls =
    chip.kind === "ready"
      ? "naml-lanes__chip naml-lanes__chip--ready"
      : "naml-lanes__chip";
  return (
    <span className={cls} data-kind={chip.kind}>
      <span className="naml-lanes__chip-id">{chip.sliceId}</span>
      {chip.detail}
    </span>
  );
}

export function Lanes({ data = lanesFixture }: LanesProps) {
  return (
    <section className="naml-lanes" aria-label="Lanes view">
      <div className="naml-lanes__tag">TIME · gantt by lane</div>
      <div className="naml-lanes__gantt">
        <div className="naml-lanes__gtime" role="presentation">
          {data.timeAxis.map((label, i) => {
            const isNow = label.toLowerCase().includes("now");
            const isEta = label.toLowerCase().includes("eta");
            const cls = isNow
              ? "naml-lanes__gtime-tick--now"
              : isEta
                ? "naml-lanes__gtime-tick--eta"
                : undefined;
            return (
              <span key={`${label}-${i}`} className={cls}>
                {label}
              </span>
            );
          })}
        </div>
        {data.tracks.map((t) => (
          <LaneRow key={t.laneId} track={t} nowPct={data.nowPct} />
        ))}
      </div>

      <div className="naml-lanes__tag">NOW · current state per lane</div>
      <div className="naml-lanes__cards">
        {data.cards.map((c) => (
          <Card key={c.laneId} card={c} />
        ))}
      </div>

      <div className="naml-lanes__queue" aria-label="Queue">
        <span className="naml-lanes__queue-label">QUEUE</span>
        {data.queue.map((q) => (
          <ChipEl key={q.sliceId} chip={q} />
        ))}
        <span className="naml-lanes__queue-spacer" />
        <span className="naml-lanes__queue-summary">{data.queueSummary}</span>
      </div>
    </section>
  );
}
