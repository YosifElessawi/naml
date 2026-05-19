import { useEffect, useState } from "react";

export type Route =
  | { name: "dashboard" }
  | { name: "sprint"; sprintId: string }
  | { name: "settings"; tab: string }
  | { name: "playground" };

export const DEFAULT_ROUTE: Route = { name: "dashboard" };

const PLAYGROUND_PATH = "/playground";

export function parsePath(path: string): Route {
  const clean = path.replace(/^#/, "").replace(/\?.*$/, "");
  if (clean === "" || clean === "/" || clean === "/dashboard") {
    return { name: "dashboard" };
  }
  if (clean === PLAYGROUND_PATH) {
    return { name: "playground" };
  }
  const sprintMatch = clean.match(/^\/sprint\/([^/]+)$/);
  if (sprintMatch?.[1]) {
    return { name: "sprint", sprintId: decodeURIComponent(sprintMatch[1]) };
  }
  const settingsMatch = clean.match(/^\/settings(?:\/([^/]+))?$/);
  if (settingsMatch) {
    return { name: "settings", tab: settingsMatch[1] ?? "general" };
  }
  return DEFAULT_ROUTE;
}

export function routeToPath(route: Route): string {
  switch (route.name) {
    case "dashboard":
      return "/dashboard";
    case "sprint":
      return `/sprint/${encodeURIComponent(route.sprintId)}`;
    case "settings":
      return `/settings/${encodeURIComponent(route.tab)}`;
    case "playground":
      return PLAYGROUND_PATH;
  }
}

function currentLocationPath(): string {
  if (typeof window === "undefined") return "/dashboard";
  // Hash-based routing keeps the app static-host-friendly (file://, aiohttp
  // serves index.html from a single mount point — no server-side rewrites).
  const hash = window.location.hash;
  return hash ? hash.slice(1) : "/dashboard";
}

export function useRoute(): [Route, (next: Route) => void] {
  const [route, setRoute] = useState<Route>(() => parsePath(currentLocationPath()));

  useEffect(() => {
    const onChange = () => setRoute(parsePath(currentLocationPath()));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = (next: Route) => {
    const nextPath = routeToPath(next);
    if (typeof window !== "undefined") {
      window.location.hash = nextPath;
    }
    setRoute(next);
  };

  return [route, navigate];
}
