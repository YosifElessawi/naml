import { useEffect, useState } from "react";
import { type Store, store as defaultStore } from "../../store/store.ts";
import type { NamlStore } from "../../store/types.ts";
import { SyncDot } from "../SyncDot/index.ts";
import styles from "./Header.module.css";

export type HeaderTab = "dashboard" | "sprint" | "settings";

export interface HeaderProps {
  activeTab: HeaderTab;
  onTabChange: (tab: HeaderTab) => void;
  /** Inject a store for tests. Defaults to the singleton populated by SSE. */
  store?: Store;
  /** Override displayed project name. Default = the naml repo slug. */
  projectLabel?: string;
  hasNotifications?: boolean;
  accountInitials?: string;
  accountLabel?: string;
}

const TAB_ORDER: HeaderTab[] = ["dashboard", "sprint", "settings"];
const TAB_LABEL: Record<HeaderTab, string> = {
  dashboard: "Dashboard",
  sprint: "Sprint",
  settings: "Settings",
};

interface SprintPin {
  name: string;
  done: number;
  total: number;
}

function pickPin(s: NamlStore): SprintPin | null {
  const sprints = Object.values(s.sprints);
  if (sprints.length === 0) return null;
  // Pick the most recently updated sprint as the pin.
  const sorted = sprints.slice().sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
  const top = sorted[0];
  if (!top) return null;
  return { name: top.id, done: top.slicesDone, total: top.slicesTotal };
}

export function Header({
  activeTab,
  onTabChange,
  store = defaultStore,
  projectLabel = "YosifElessawi/naml",
  hasNotifications = true,
  accountInitials = "YE",
  accountLabel = "personal",
}: HeaderProps) {
  const [pin, setPin] = useState<SprintPin | null>(() => pickPin(store.getState()));
  useEffect(() => {
    const pull = () => setPin(pickPin(store.getState()));
    const off = store.subscribeKey("sprints", pull);
    pull();
    return off;
  }, [store]);

  const sprintName = pin?.name ?? "—";
  const sprintDone = pin?.done ?? 0;
  const sprintTotal = pin?.total ?? 0;
  const progress = sprintTotal > 0 ? Math.min(100, (sprintDone / sprintTotal) * 100) : 0;

  return (
    <header className={styles.hdr}>
      <span className={styles.logo} aria-label="naml">
        ▟ naml
      </span>
      <span className={styles.sync} aria-live="polite">
        <SyncDot store={store} />
      </span>
      <span className={styles.div} aria-hidden="true" />
      <button type="button" className={styles.switcher} aria-label="Switch project">
        {projectLabel} <span className={styles.switcherCaret}>▾</span>
      </button>
      <button
        type="button"
        className={styles.sprintPin}
        aria-label={`Jump to sprint ${sprintName}`}
      >
        <span className={styles.play} aria-hidden="true" />
        {sprintName}
        <span className={styles.prog} aria-hidden="true">
          <span className={styles.progFill} style={{ width: `${progress}%` }} />
        </span>
        <span className={styles.sprintCount}>
          {sprintDone}/{sprintTotal}
        </span>
      </button>
      <span className={styles.grow} aria-hidden="true" />
      <nav className={styles.tabs} role="tablist" aria-label="Main views">
        {TAB_ORDER.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            className={`${styles.tab} ${activeTab === tab ? styles.tabActive : ""}`}
            onClick={() => onTabChange(tab)}
          >
            {TAB_LABEL[tab]}
          </button>
        ))}
      </nav>
      <button
        type="button"
        className={styles.iconbtn}
        aria-label={hasNotifications ? "Notifications (unread)" : "Notifications"}
      >
        <span aria-hidden="true">🔔</span>
        {hasNotifications ? <span className={styles.ndot} aria-hidden="true" /> : null}
      </button>
      <button type="button" className={styles.acct} aria-label={`Account: ${accountLabel}`}>
        <span className={styles.av} aria-hidden="true">
          {accountInitials}
        </span>
        {accountLabel}
      </button>
      <button type="button" className={styles.iconbtn} aria-label="Settings">
        <span aria-hidden="true">⚙</span>
      </button>
    </header>
  );
}
