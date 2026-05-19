import type { SliceDrawerData, SliceState } from "./types";

const baseAcceptance: SliceDrawerData["acceptance"] = [
  {
    text: "Renderer accepts the V2 config schema from slice-3",
    status: "done",
  },
  {
    text: "Output matches the mock-target snapshot in artifacts/mock-output.html",
    status: "done",
  },
  {
    text: "Uses <strong> tags, not <b> (accessibility)",
    status: "done",
  },
  {
    text: "All snapshot tests in tests/render/snapshots/ pass",
    status: "run",
    note: "typecheck failing",
  },
  {
    text: "Renders in storybook under render/HtmlRenderer",
    status: "pending",
  },
];

const baseTraversal: SliceDrawerData["traversal"] = [
  {
    state: "setup",
    status: "done",
    timestamp: "14:17:43",
    duration: "+4s",
    detail: "Worktree provisioned · branch naml/slice-4-html-renderer created from main",
  },
  {
    state: "work",
    status: "done",
    timestamp: "14:17:47",
    duration: "4m 30s",
    detail: "Implementer A1 spawned · session 9b0c…ef13 · 11 turns · gates green up to typecheck",
  },
  {
    state: "WORK · RETRY 1/2",
    status: "retry",
    timestamp: "14:22:17",
    duration: "resume",
    gateFail: true,
    detail: "typecheck failed · resume sent with stderr · A1 session re-entered",
  },
  {
    state: "work",
    status: "current",
    timestamp: "14:22:18",
    duration: "running · 1m 41s",
    detail: "A1 reasoning about renderer.ts type mismatch · 3 turns since resume",
  },
  { state: "pr", status: "pending" },
  { state: "review", status: "pending" },
  { state: "merged", status: "pending" },
];

const baseGateOutput: SliceDrawerData["gateOutput"] = {
  command: "pnpm typecheck",
  failedAt: "14:22:14",
  body: `▶ pnpm typecheck

src/render/html/renderer.ts:42:18 - error TS2345: Argument of type
  'SchemaConfig | undefined' is not assignable to parameter of type 'SchemaConfig'.
  Type 'undefined' is not assignable to type 'SchemaConfig'.

42     return formatNode(node.config, ctx);
                          ~~~~~~~~~~~

Found 1 error in src/render/html/renderer.ts:42

✗ typecheck failed (exit 1)
⏵ resuming A1 with error context (retry 1/2)`,
};

export const drawerFixtureWork: SliceDrawerData = {
  id: "slice-4",
  title: "HTML renderer with mock-target snapshot",
  state: "work",
  lane: "lane-2",
  afk: true,
  startedAt: "14:17:43",
  durationLabel: "6m12s",
  sprintId: "2026-05-19-html-renderer",
  sessionId: "9b0c-4a82-f3d8-ef13",
  branch: "naml/slice-4-html-renderer",
  worktree: "~/Library/Logs/agents-orchestrator/wt/lane-2",
  dependsOn: "slice-2 ✓ · slice-3 ⏵",
  touches: "src/render/html/** · tests/render/html/**",
  prUrl: null,
  cost: 0.41,
  tokens: { input: 82_000, output: 14_000, cache: 1_200_000 },
  context: { used: 146_000, max: 200_000 },
  retry: { count: 1, max: 2 },
  duration: { elapsedSec: 372, budgetSec: 540 },
  acceptance: baseAcceptance,
  traversal: baseTraversal,
  gateOutput: baseGateOutput,
  review: { verdict: "pending", body: "" },
  notes:
    "Renderer must round-trip the mock-target HTML exactly — no semantic differences. " +
    "Pay attention to <strong> vs <b> (accessibility ask from the feedback inbox). " +
    "Snapshot tests live in tests/render/snapshots/; regenerate only if the AC explicitly " +
    "says so. The HTML renderer is the slice that unlocks the eval harness in slice-5.",
};

export const drawerFixtureReview: SliceDrawerData = {
  ...drawerFixtureWork,
  state: "review",
  prUrl: "https://github.com/example/naml/pull/42",
  review: {
    verdict: "lgtm",
    body:
      "All acceptance criteria met. Snapshot tests are green; typecheck passes after the " +
      "retry. <strong> usage is consistent. Minor nit on inline comments but no blocker.",
  },
};

export const drawerFixtureHeld: SliceDrawerData = {
  ...drawerFixtureWork,
  state: "held",
  heldReason: "paused by user · terminal unlocked",
};

export const drawerFixturesByState: Record<SliceState, SliceDrawerData> = {
  pending: { ...drawerFixtureWork, state: "pending", sessionId: undefined },
  setup: { ...drawerFixtureWork, state: "setup" },
  work: drawerFixtureWork,
  pr: { ...drawerFixtureWork, state: "pr", prUrl: "https://github.com/example/naml/pull/42" },
  review: drawerFixtureReview,
  merged: {
    ...drawerFixtureReview,
    state: "merged",
    review: { verdict: "lgtm", body: drawerFixtureReview.review?.body ?? "" },
  },
  failed: { ...drawerFixtureWork, state: "failed" },
  needs_human_review: { ...drawerFixtureWork, state: "needs_human_review" },
  blocked_upstream: { ...drawerFixtureWork, state: "blocked_upstream" },
  abandoned: { ...drawerFixtureWork, state: "abandoned" },
  held: drawerFixtureHeld,
};
