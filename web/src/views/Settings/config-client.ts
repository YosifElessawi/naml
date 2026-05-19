import type { NamlSettings } from "./types.ts";

// Fixture settings — used until slice-10 ships GET /config. Reflects the
// shape of .naml/config.toml plus UI-only fields (labels, feature flags).
export const FIXTURE_SETTINGS: NamlSettings = {
  project: {
    repoSlug: "YosifElessawi/naml",
    baseBranch: "main",
    projectRoot: "/Users/yosifelessawi/Desktop/Naml",
    adrFolder: "docs/adrs/",
    lifetimeSince: "2026-03-14",
    labels: {
      sprintPrefix: "sprint:",
      slicePrefix: "slice:",
      lifecycle: [
        "naml:running",
        "naml:review",
        "naml:human-review",
        "naml:done",
      ],
    },
  },
  lanes: {
    defaultLanes: 3,
    dagWidthDetection: true,
    hardCap: 8,
  },
  gates: [
    { name: "lint", argv: "pnpm lint", status: "ok", ranAt: "14:21" },
    { name: "typecheck", argv: "pnpm typecheck", status: "ok" },
    { name: "test", argv: "pnpm test --run", status: "running" },
    { name: "build", argv: "pnpm build", status: "idle" },
  ],
  account: {
    configDir: "~/.claude",
    configDirOptions: [
      "~/.claude · default",
      "~/.claude-personal · personal",
      "~/.claude-work · work",
    ],
    model: "claude-opus-4-7",
    modelOptions: [
      "claude-opus-4-7",
      "claude-sonnet-4-6",
      "claude-haiku-4-5",
    ],
    contextWindow: 200_000,
    sessionTokenLimit: null,
    weeklyTokenLimit: null,
    sessionResetAt: null,
    weeklyResetAt: null,
  },
  sync: {
    heartbeatSeconds: 2,
    slowThresholdSeconds: 5,
    lostThresholdSeconds: 15,
    reduceMotion: false,
  },
  advanced: {
    featureFlags: {
      "held-state": true,
      "jsonl-compaction": false,
      "experimental-replay": false,
    },
    rawConfigToml: "",
  },
};

const CONFIG_URL = "/config";

async function safeFetch(
  url: string,
  init?: RequestInit,
): Promise<Response | null> {
  try {
    return await fetch(url, init);
  } catch {
    return null;
  }
}

/**
 * GET /config — falls back to the fixture when the server endpoint is not
 * yet available (slice-10 introduces the real handler). Returning a
 * fixture rather than throwing keeps the UI usable in development with
 * just the static `vite` dev server.
 */
export async function loadSettings(): Promise<NamlSettings> {
  const res = await safeFetch(CONFIG_URL, { headers: { accept: "application/json" } });
  if (!res || !res.ok) {
    return FIXTURE_SETTINGS;
  }
  try {
    const data = (await res.json()) as Partial<NamlSettings>;
    return { ...FIXTURE_SETTINGS, ...data };
  } catch {
    return FIXTURE_SETTINGS;
  }
}

/**
 * POST /config — atomic config rewrite is handled by the server (see
 * slice description). Returns true on success. When the endpoint is
 * absent, the call is treated as a local-only save so autosave still
 * gives visual feedback.
 */
export async function saveSettings(
  partial: Partial<NamlSettings>,
): Promise<boolean> {
  const res = await safeFetch(CONFIG_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(partial),
  });
  if (!res) {
    return true;
  }
  return res.ok;
}

export async function resetAggregates(): Promise<boolean> {
  const res = await safeFetch("/aggregates/reset", { method: "POST" });
  return res?.ok ?? false;
}
