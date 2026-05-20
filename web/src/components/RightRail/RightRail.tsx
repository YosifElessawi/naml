import { ActivityTicker } from "../ActivityTicker/index.ts";
import { CostTimeline } from "../CostTimeline/index.ts";
import { SyncDot } from "../SyncDot/index.ts";
import styles from "./RightRail.module.css";

// Right rail re-wired against slice-13's live components. Each child
// subscribes to the singleton store on its own; the rail is just a
// layout shell. Session / weekly quota stubs stay hand-coded for now —
// quota is sourced from Anthropic's API, not naml, and that wiring is
// out of scope until the cockpit has an account-status endpoint.

export interface RightRailProps {
  collapsed: boolean;
}

export function RightRail({ collapsed }: RightRailProps) {
  if (collapsed) {
    return (
      <aside
        className={`${styles.rail} ${styles.railCollapsed}`}
        aria-label="Telemetry rail (collapsed)"
      >
        <SyncDot compact />
      </aside>
    );
  }

  return (
    <aside className={styles.rail} aria-label="Telemetry rail">
      <div className={styles.seg}>
        <div className={styles.tag}>
          <span>SYNC</span>
          <SyncDot />
        </div>
        <ActivityTicker limit={6} />
      </div>

      <div className={styles.seg}>
        <CostTimeline />
      </div>

      <div className={styles.seg}>
        <div className={styles.tag}>SESSION QUOTA</div>
        <div className={styles.row}>
          <span className={styles.label}>used</span>
          <span className={styles.value}>—</span>
        </div>
        <div className={styles.reset}>
          Anthropic account quota (wire in follow-up; out of scope for cockpit-v2)
        </div>
      </div>

      <div className={styles.seg}>
        <div className={styles.tag}>WEEKLY QUOTA</div>
        <div className={styles.row}>
          <span className={styles.label}>used</span>
          <span className={styles.value}>—</span>
        </div>
        <div className={styles.reset}>Anthropic account quota (wire in follow-up)</div>
      </div>
    </aside>
  );
}
