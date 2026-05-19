import { useEffect, useState } from "react";
import { Header, type HeaderTab } from "../Header/Header.tsx";
import { RightRail } from "../RightRail/RightRail.tsx";
import { type Route, useRoute } from "../../router/router.ts";
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

function MainPane({ route }: { route: Route }) {
  switch (route.name) {
    case "dashboard":
      return (
        <section className={styles.placeholder} aria-label="Dashboard">
          <span className={styles.routeTag}>DASHBOARD</span>
          <h1>Dashboard</h1>
          <p>
            Project hero + cost cluster + health row land in slice-3. This is the route
            placeholder.
          </p>
        </section>
      );
    case "sprint":
      return (
        <section className={styles.placeholder} aria-label="Sprint">
          <span className={styles.routeTag}>SPRINT</span>
          <h1>Sprint · {route.sprintId}</h1>
          <p>
            Sprint header, lanes, and slice grid land in slices 4–6. This is the route
            placeholder.
          </p>
        </section>
      );
    case "settings":
      return (
        <section className={styles.placeholder} aria-label="Settings">
          <span className={styles.routeTag}>SETTINGS · {route.tab.toUpperCase()}</span>
          <h1>Settings · {route.tab}</h1>
          <p>Settings tabs (general, accounts, projects, health) land in slice-8.</p>
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
        <Header
          activeTab={routeToTab(route)}
          onTabChange={(tab) => navigate(tabToRoute(tab))}
        />
      </div>
      <main className={styles.main} tabIndex={-1}>
        <MainPane route={route} />
      </main>
      <div className={styles.rail}>
        <RightRail collapsed={railCollapsed} />
      </div>
    </div>
  );
}
