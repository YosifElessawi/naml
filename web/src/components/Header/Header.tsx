import styles from "./Header.module.css";

export type HeaderTab = "dashboard" | "sprint" | "settings";

export interface HeaderProps {
  activeTab: HeaderTab;
  onTabChange: (tab: HeaderTab) => void;
  syncLabel?: string;
  projectLabel?: string;
  sprintName?: string;
  sprintDone?: number;
  sprintTotal?: number;
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

export function Header({
  activeTab,
  onTabChange,
  syncLabel = "Synced 2s",
  projectLabel = "inpolicy/web",
  sprintName = "2026-05-19-html-renderer",
  sprintDone = 5,
  sprintTotal = 8,
  hasNotifications = true,
  accountInitials = "YE",
  accountLabel = "personal",
}: HeaderProps) {
  const progress = sprintTotal > 0 ? Math.min(100, (sprintDone / sprintTotal) * 100) : 0;

  return (
    <header className={styles.hdr}>
      <span className={styles.logo} aria-label="naml">
        ▟ naml
      </span>
      <span className={styles.sync} aria-live="polite">
        <span className={`${styles.ddot} naml-pulse`} aria-hidden="true" />
        {syncLabel}
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
