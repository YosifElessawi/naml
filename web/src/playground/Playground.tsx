import { useState } from "react";
import { Header, type HeaderTab } from "../components/Header/Header.tsx";
import { RightRail, type SyncState } from "../components/RightRail/RightRail.tsx";
import styles from "./Playground.module.css";

const SYNC_STATES: SyncState[] = ["live", "slow", "lost", "connecting"];

export function Playground() {
  const [tab, setTab] = useState<HeaderTab>("dashboard");
  const [sync, setSync] = useState<SyncState>("live");
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className={styles.page}>
      <div>
        <h1 className={styles.title}>naml cockpit · component playground</h1>
        <p className={styles.lede}>
          Each shell piece in isolation — useful for tuning visual fidelity against the mockups
          without booting the rest of the app. Open via <code>#/playground</code>.
        </p>
      </div>

      <section className={styles.section}>
        <span className={styles.sectionTag}>HEADER</span>
        <div className={styles.frame}>
          <Header activeTab={tab} onTabChange={setTab} />
        </div>
        <div className={styles.actions}>
          {(["dashboard", "sprint", "settings"] as HeaderTab[]).map((t) => (
            <button key={t} type="button" onClick={() => setTab(t)}>
              activate {t}
            </button>
          ))}
        </div>
      </section>

      <section className={styles.section}>
        <span className={styles.sectionTag}>RIGHT RAIL · expanded</span>
        <div className={styles.frame}>
          <div className={styles.frameRow}>
            <div style={{ flex: 1, background: "var(--bg-0)" }} />
            <RightRail collapsed={false} syncState={sync} />
          </div>
        </div>
        <div className={styles.actions}>
          {SYNC_STATES.map((s) => (
            <button key={s} type="button" onClick={() => setSync(s)}>
              sync={s}
            </button>
          ))}
        </div>
      </section>

      <section className={styles.section}>
        <span className={styles.sectionTag}>RIGHT RAIL · collapsed</span>
        <div className={styles.frame}>
          <div className={styles.frameRow}>
            <div style={{ flex: 1, background: "var(--bg-0)" }} />
            <RightRail collapsed={collapsed} syncState={sync} />
          </div>
        </div>
        <div className={styles.actions}>
          <button type="button" onClick={() => setCollapsed((v) => !v)}>
            toggle collapsed (now {collapsed ? "ON" : "OFF"})
          </button>
        </div>
      </section>
    </div>
  );
}
