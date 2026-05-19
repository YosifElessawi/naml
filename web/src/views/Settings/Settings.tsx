import { Fragment, useEffect, useMemo, useState } from "react";
import { Account } from "./Account/Account.tsx";
import { Advanced } from "./Advanced/Advanced.tsx";
import { Gates } from "./Gates/Gates.tsx";
import { Health as HealthTab } from "./Health/Health.tsx";
import { Lanes } from "./Lanes/Lanes.tsx";
import { Project } from "./Project/Project.tsx";
import css from "./Settings.module.css";
import { Sync } from "./Sync/Sync.tsx";
import { SavedBadge } from "./components/widgets.tsx";
import { FIXTURE_SETTINGS, loadSettings } from "./config-client.ts";
import { TAB_ORDER, type TabId } from "./types.ts";
import { useAutosave } from "./useAutosave.ts";

interface NavEntry {
  id: TabId;
  label: string;
  icon: string;
  badge?: { label: string; tone?: "default" | "trends" };
}

const NAV: readonly NavEntry[] = [
  { id: "project", label: "Project", icon: "●" },
  { id: "lanes", label: "Lanes", icon: "▤" },
  { id: "gates", label: "Gates", icon: "▣" },
  { id: "account", label: "Account", icon: "◐", badge: { label: "multi" } },
  { id: "sync", label: "Sync", icon: "⌁" },
  { id: "health", label: "Health", icon: "▴", badge: { label: "trends", tone: "trends" } },
  { id: "advanced", label: "Advanced", icon: "⚙" },
] as const;

function isTabId(v: string): v is TabId {
  return (TAB_ORDER as readonly string[]).includes(v);
}

interface SettingsProps {
  /** Active tab. When undefined the component manages its own state. */
  tab?: string;
  /** Notified when the user clicks a nav item so the parent can update the URL. */
  onTabChange?: (tab: TabId) => void;
}

export function Settings({ tab, onTabChange }: SettingsProps) {
  const [internalTab, setInternalTab] = useState<TabId>("project");
  const activeTab: TabId = useMemo(() => {
    if (tab && isTabId(tab)) return tab;
    return internalTab;
  }, [tab, internalTab]);

  const [loaded, setLoaded] = useState(FIXTURE_SETTINGS);
  useEffect(() => {
    let cancelled = false;
    loadSettings().then((settings) => {
      if (!cancelled) setLoaded(settings);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const { draft, updateSection, flush, savedAt } = useAutosave(loaded);

  function selectTab(next: TabId) {
    setInternalTab(next);
    onTabChange?.(next);
  }

  return (
    <section className={css.root} aria-label="settings">
      <nav className={css.nav} aria-label="settings sections">
        {NAV.map((entry) => {
          // Sep between Sync and Health; sep between Health and Advanced.
          const sepBefore = entry.id === "health" || entry.id === "advanced";
          return (
            <Fragment key={entry.id}>
              {sepBefore ? <div className={css.navSep} /> : null}
              <button
                type="button"
                className={`${css.navItem} ${entry.id === activeTab ? css.active : ""}`}
                aria-current={entry.id === activeTab ? "page" : undefined}
                onClick={() => selectTab(entry.id)}
                data-tab={entry.id}
              >
                <span className={css.navIcon}>{entry.icon}</span>
                <span>{entry.label}</span>
                {entry.badge ? (
                  <span
                    className={`${css.navBadge} ${entry.badge.tone === "trends" ? css.trends : ""}`}
                  >
                    {entry.badge.label}
                  </span>
                ) : null}
              </button>
            </Fragment>
          );
        })}
        <div className={css.navSep} />
        <button
          type="button"
          className={`${css.navItem} ${css.navAdd}`}
          onClick={() => {
            // Mockup behavior: launches the /setup-naml CLI skill. The web UI
            // surfaces the entry point but cannot drive an interactive terminal
            // session — this hands the user back to their shell.
            window.dispatchEvent(new CustomEvent("naml:add-project"));
          }}
        >
          <span className={css.navIcon}>+</span>
          <span>Add project…</span>
        </button>
      </nav>

      <div className={css.panes}>
        <header className={css.tabHeader}>
          <span className={css.tabTitle}>
            Settings · {NAV.find((n) => n.id === activeTab)?.label ?? "—"}
          </span>
          <SavedBadge savedAt={savedAt} />
        </header>

        {activeTab === "project" ? (
          <Project
            value={draft.project}
            onChange={(v) => updateSection("project", v)}
            onCommit={flush}
          />
        ) : null}
        {activeTab === "lanes" ? (
          <Lanes value={draft.lanes} onChange={(v) => updateSection("lanes", v)} onCommit={flush} />
        ) : null}
        {activeTab === "gates" ? (
          <Gates value={draft.gates} onChange={(v) => updateSection("gates", v)} onCommit={flush} />
        ) : null}
        {activeTab === "account" ? (
          <Account
            value={draft.account}
            onChange={(v) => updateSection("account", v)}
            onCommit={flush}
          />
        ) : null}
        {activeTab === "sync" ? (
          <Sync value={draft.sync} onChange={(v) => updateSection("sync", v)} onCommit={flush} />
        ) : null}
        {activeTab === "health" ? <HealthTab /> : null}
        {activeTab === "advanced" ? (
          <Advanced
            value={draft.advanced}
            onChange={(v) => updateSection("advanced", v)}
            onCommit={flush}
          />
        ) : null}
      </div>
    </section>
  );
}
