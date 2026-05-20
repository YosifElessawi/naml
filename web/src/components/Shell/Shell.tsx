import { useEffect, useState } from "react";
import { type Route, useRoute } from "../../router/router.ts";
import { Settings } from "../../views/Settings/index.ts";
import { Lanes } from "../../views/Sprint/Lanes/index.ts";
import { Stepper } from "../../views/Sprint/Stepper/index.ts";
import { Dashboard } from "../Dashboard/Dashboard.tsx";
import { Header, type HeaderTab } from "../Header/Header.tsx";
import { RightRail } from "../RightRail/RightRail.tsx";
import styles from "./Shell.module.css";

function routeToTab(route: Route): HeaderTab {
  switch (route.name) {
    case "sprint":
      return "sprint";
    case "settings":
      return "settings";
    default:
      return "dashboard";
  }
}

function tabToRoute(tab: HeaderTab): Route {
  switch (tab) {
    case "sprint":
      // Placeholder sprint id until slice-4/5/6 wires real selection.
      return { name: "sprint", sprintId: "current" };
    case "settings":
      return { name: "settings", tab: "general" };
    default:
      return { name: "dashboard" };
  }
}

function MainPane({ route, navigate }: { route: Route; navigate: (r: Route) => void }) {
  switch (route.name) {
    case "dashboard":
      return <Dashboard />;
    case "sprint":
      return (
        <section className={styles.sprintPane} aria-label="Sprint">
          <Stepper />
          <Lanes />
        </section>
      );
    case "settings":
      return (
        <section className={styles.settingsPane} aria-label="Settings">
          <Settings tab={route.tab} onTabChange={(tab) => navigate({ name: "settings", tab })} />
        </section>
      );
    case "playground":
      return null;
  }
}

export function Shell() {
  const [route, navigate] = useRoute();
  const [railCollapsed, setRailCollapsed] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // ⌘\ on macOS, Ctrl+\ elsewhere. Either modifier alone (without Shift/Alt)
      // toggles the rail.
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key === "\\") {
        e.preventDefault();
        setRailCollapsed((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className={styles.shell}>
      <div className={styles.header}>
        <Header activeTab={routeToTab(route)} onTabChange={(tab) => navigate(tabToRoute(tab))} />
      </div>
      <main className={styles.main} tabIndex={-1}>
        <MainPane route={route} navigate={navigate} />
      </main>
      <div className={styles.rail}>
        <RightRail collapsed={railCollapsed} />
      </div>
    </div>
  );
}
