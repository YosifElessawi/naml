import { describe, expect, it } from "vitest";
import type { GateRow } from "../types.ts";
import { sortGatesByCost } from "./Gates.tsx";

describe("sortGatesByCost", () => {
  it("places lint < typecheck < test < build", () => {
    const gates: GateRow[] = [
      { name: "build", argv: "pnpm build", status: "idle" },
      { name: "test", argv: "pnpm test --run", status: "idle" },
      { name: "lint", argv: "pnpm lint", status: "ok" },
      { name: "typecheck", argv: "pnpm typecheck", status: "running" },
    ];
    expect(sortGatesByCost(gates).map((g) => g.name)).toEqual([
      "lint",
      "typecheck",
      "test",
      "build",
    ]);
  });

  it("treats unknown gates as most expensive", () => {
    const gates: GateRow[] = [
      { name: "smoke", argv: "pnpm smoke", status: "idle" },
      { name: "lint", argv: "pnpm lint", status: "ok" },
    ];
    expect(sortGatesByCost(gates).map((g) => g.name)).toEqual(["lint", "smoke"]);
  });

  it("recognises the web-* gate naming used in this repo", () => {
    const gates: GateRow[] = [
      { name: "web-build", argv: "pnpm build", status: "idle" },
      { name: "web-test", argv: "pnpm test --run", status: "idle" },
      { name: "web-typecheck", argv: "pnpm typecheck", status: "idle" },
      { name: "web-lint", argv: "pnpm lint", status: "idle" },
    ];
    expect(sortGatesByCost(gates).map((g) => g.name)).toEqual([
      "web-lint",
      "web-typecheck",
      "web-test",
      "web-build",
    ]);
  });
});
