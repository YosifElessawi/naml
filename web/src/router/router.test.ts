import { describe, expect, it } from "vitest";
import { parsePath, routeToPath } from "./router.ts";

describe("parsePath", () => {
  it("treats empty + root + /dashboard as the dashboard", () => {
    expect(parsePath("")).toEqual({ name: "dashboard" });
    expect(parsePath("/")).toEqual({ name: "dashboard" });
    expect(parsePath("/dashboard")).toEqual({ name: "dashboard" });
  });

  it("parses /sprint/:id", () => {
    expect(parsePath("/sprint/2026-05-19-cockpit-v2")).toEqual({
      name: "sprint",
      sprintId: "2026-05-19-cockpit-v2",
    });
  });

  it("parses /settings with default tab + named tab", () => {
    expect(parsePath("/settings")).toEqual({ name: "settings", tab: "general" });
    expect(parsePath("/settings/health")).toEqual({ name: "settings", tab: "health" });
  });

  it("falls back to dashboard for unknown paths", () => {
    expect(parsePath("/garbage")).toEqual({ name: "dashboard" });
  });

  it("strips a leading hash if present", () => {
    expect(parsePath("#/sprint/x")).toEqual({ name: "sprint", sprintId: "x" });
  });
});

describe("routeToPath", () => {
  it("round-trips through parsePath", () => {
    const routes = [
      { name: "dashboard" as const },
      { name: "sprint" as const, sprintId: "2026-05-19-cockpit-v2" },
      { name: "settings" as const, tab: "health" },
      { name: "playground" as const },
    ];
    for (const r of routes) {
      expect(parsePath(routeToPath(r))).toEqual(r);
    }
  });
});
