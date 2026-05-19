import styles from "./RightRail.module.css";

export type SyncState = "live" | "slow" | "lost" | "connecting";

export interface ActivityEntry {
  ts: string;
  body: string;
  sliceId?: string;
}

export interface RightRailProps {
  collapsed: boolean;
  syncState?: SyncState;
  syncLabel?: string;
  activity?: ActivityEntry[];
}

const DEFAULT_ACTIVITY: ActivityEntry[] = [
  { ts: "14:23", sliceId: "slice-3", body: "→ review" },
  { ts: "14:21", sliceId: "slice-2", body: "merged ✓" },
  { ts: "14:18", body: "lane-3 idle" },
];

// Pre-baked sparkline that matches the q7b mockup so the rail reads correctly
// before slice-13 wires it to live data.
const SPARK_BARS: { id: string; height: number; tone?: "hi" | "warn" }[] = [
  { id: "d-14", height: 18 },
  { id: "d-13", height: 32 },
  { id: "d-12", height: 24 },
  { id: "d-11", height: 38 },
  { id: "d-10", height: 60 },
  { id: "d-9", height: 28 },
  { id: "d-8", height: 44 },
  { id: "d-7", height: 70 },
  { id: "d-6", height: 52 },
  { id: "d-5", height: 36 },
  { id: "d-4", height: 48 },
  { id: "d-3", height: 76 },
  { id: "d-2", height: 92, tone: "warn" },
  { id: "d-1", height: 64, tone: "hi" },
];

function CollapsedDot({ state }: { state: SyncState }) {
  const variant =
    state === "slow"
      ? styles.collapsedDotSlow
      : state === "lost"
        ? styles.collapsedDotLost
        : state === "connecting"
          ? styles.collapsedDotConnecting
          : "";
  return (
    <span
      className={`${styles.collapsedDot} ${variant} naml-pulse`}
      aria-label={`Sync ${state}`}
      role="status"
    />
  );
}

export function RightRail({
  collapsed,
  syncState = "live",
  syncLabel = "2s ago",
  activity = DEFAULT_ACTIVITY,
}: RightRailProps) {
  if (collapsed) {
    return (
      <aside
        className={`${styles.rail} ${styles.railCollapsed}`}
        aria-label="Telemetry rail (collapsed)"
      >
        <CollapsedDot state={syncState} />
      </aside>
    );
  }

  return (
    <aside className={styles.rail} aria-label="Telemetry rail">
      <div className={styles.seg}>
        <div className={styles.tag}>
          <span>SYNC</span>
          <span className={styles.tagAccent}>▢ {syncLabel}</span>
        </div>
        <div className={styles.activity}>
          {activity.map((entry) => (
            <div
              key={`${entry.ts}-${entry.sliceId ?? "_"}-${entry.body}`}
              className={styles.activityRow}
              data-testid="activity-row"
            >
              <span className={styles.activityTs}>{entry.ts}</span>
              <span className={styles.activityEv}>
                {entry.sliceId ? (
                  <>
                    <span className={styles.sid}>{entry.sliceId}</span>{" "}
                  </>
                ) : null}
                {entry.body}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className={styles.seg}>
        <div className={styles.tag}>
          <span>COST TIMELINE</span>
          <span className={styles.tagAccentCool}>▴</span>
        </div>
        <div className={styles.costBox}>
          <div className={styles.row}>
            <span className={styles.label}>today</span>
            <span className={styles.value}>$4.21</span>
          </div>
          <div className={styles.row}>
            <span className={styles.label}>this week</span>
            <span className={styles.value}>$24.80</span>
          </div>
          <div className={styles.row}>
            <span className={styles.label}>last 30d</span>
            <span className={styles.value}>$118.42</span>
          </div>
          <div className={`${styles.row} ${styles.lifetimeRow}`}>
            <span className={styles.label}>lifetime</span>
            <span className={`${styles.value} ${styles.valueLifetime}`}>$342.16</span>
          </div>
        </div>
        <div className={styles.spark} aria-hidden="true">
          {SPARK_BARS.map((bar) => (
            <i
              key={bar.id}
              className={`${styles.sparkBar} ${
                bar.tone === "warn"
                  ? styles.sparkBarWarn
                  : bar.tone === "hi"
                    ? styles.sparkBarHi
                    : ""
              }`}
              style={{ height: `${bar.height}%` }}
            />
          ))}
        </div>
        <div className={styles.sparkAxis}>
          <span>14d ago</span>
          <span>today</span>
        </div>
        <div className={styles.row} style={{ marginTop: 6 }}>
          <span className={styles.label}>tokens today</span>
          <span className={styles.value}>9.2M</span>
        </div>
        <div className={styles.row}>
          <span className={styles.label}>tokens · 30d</span>
          <span className={styles.value}>218M</span>
        </div>
      </div>

      <div className={styles.seg}>
        <div className={styles.tag}>SESSION QUOTA</div>
        <div className={styles.row}>
          <span className={styles.label}>used</span>
          <span className={styles.value}>29%</span>
        </div>
        <div className={styles.qbar} aria-hidden="true">
          <div className={`${styles.qbarFill} ${styles.qbarOk}`} style={{ width: "29%" }} />
        </div>
        <div className={styles.reset}>resets in 50m</div>
      </div>

      <div className={styles.seg}>
        <div className={styles.tag}>WEEKLY QUOTA</div>
        <div className={styles.row}>
          <span className={styles.label}>used</span>
          <span className={`${styles.value} ${styles.valueWarn}`}>84%</span>
        </div>
        <div className={styles.qbar} aria-hidden="true">
          <div className={`${styles.qbarFill} ${styles.qbarWarn}`} style={{ width: "84%" }} />
        </div>
        <div className={styles.reset}>resets in 5d 8h · est empty Thu 6pm</div>
      </div>

      <div className={styles.seg}>
        <div className={styles.tag}>THIS PROJECT</div>
        <div className={styles.row}>
          <span className={styles.label}>share of weekly</span>
          <span className={styles.value}>62%</span>
        </div>
        <div className={styles.row}>
          <span className={styles.label}>share of lifetime</span>
          <span className={styles.value}>71%</span>
        </div>
      </div>
    </aside>
  );
}
