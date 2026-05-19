# Agents Orchestrator — Roadmap & Handoff

Working name: **`agents-orchestrator`**. This is the cross-session source of
truth — read it first. The build is continuing in a different Claude session
(higher-limit account); auto-memory does not follow that switch, this file
does.

## What this is — corrected scope

A **personal developer tool**. It automates the **implementation phase** of a
spec-driven dev workflow: once issues are authored and triaged, it spawns
Claude agents to implement them, validates locally, and opens/merges PRs.

It is **not** a product, **not** tied to Deka Press, and does **not** run on
any client's machine. It runs on the developer's own machine to speed up the
developer's work. It was prototyped against the Deka Press repo, so the
current code is coupled to that project — that coupling is temporary and the
rescope below removes it.

> Things in the prototype that are **wrong** and must be corrected:
> - The name "AFK orchestrator" — unattended scheduling is just *one mode*,
>   not the tool's identity. Rename to `agents-orchestrator`.
> - `OPERATING.md` says it runs on "Mrs. Doaa's / the dev machine" — false.
>   It is purely the developer's local tool.
> - Hardcoded Deka assumptions (pnpm gates, the `@deka` repo, the
>   `YosifElessawi/Data-Managment-System` slug, `AFK_*` env vars).

## Where it fits — the workflow

1. **Authoring** — Matt Pocock skills: grilling session → PRD → `to-issues` →
   triage. Produces triaged, agent-ready issues.
2. **Implementation** — `agents-orchestrator` (this tool) picks up those
   triaged issues and drives each one to a merged PR.

This tool owns **step 2**, for any project.

## The immediate task — rename & rescope

- **Rename** `afk-orchestrator` → `agents-orchestrator` everywhere: directory,
  file paths, the launchd label, and the `AFK_*` env-var prefix (→ e.g.
  `AO_*`). Drop "afk" from the identity; keep "unattended/scheduled" as the
  name of *one mode*.
- **Make it project-agnostic.** It must work on any repo:
  - Validation gates are currently hardcoded pnpm
    (`pnpm lint/typecheck/test/build`). Move them into a **per-project config
    file** at the target repo root (e.g. `.agents-orchestrator.toml`) that
    declares: the gate commands, the repo slug, the base branch, the issue
    labels. The orchestrator reads that file; no project name lives in code.
  - Strip every Deka/`@deka`/`Data-Managment-System` assumption out of
    `config.py` into that config file or env.
- **Drop-in model.** Placed at a target repo's root, it exposes `make`
  commands and "just works." The canonical home of the tool's own source is
  `~/Desktop/Naml/`.
- Fix `OPERATING.md`'s scope framing accordingly.

## Current state (prototype — working, not yet rescoped)

Python 3 stdlib orchestrator. Today it lives **untracked** inside the Deka
repo (`scripts/afk-orchestrator/` + `/Makefile`, via `.git/info/exclude`) —
to be lifted out to `~/Desktop/Naml/` and de-coupled.

What works:
- Watches a "ready" issue label; one inner loop shared by `slow` (one issue)
  and `burst` (clear the queue).
- Each issue runs as a Claude session with an assigned **UUID**
  (`--session-id`) and display name. The UUID is the handle for everything.
- Local validation gates: `install → lint → typecheck → test → build`.
- **Resume-based auto-retry**: a failed gate resumes the agent's own session
  with the error, up to N times, before failing.
- Opens a PR, labels the issue, merges when green (unless dry-run).
- `status` dashboard prints a `claude --resume <uuid>` line per agent.
- Driven by `make` (`make` = supervised run; `make status`, etc.).

First live run failed a `typecheck` gate — which is what motivated the
auto-retry loop. Not yet run end-to-end successfully.

## Gap 1 — Multiple issues per session (batching)

Today: one session per issue. Related issues should be able to share a session
(shared context, warm prompt cache — each cold `claude -p` pays ~$0.08 of
cache-creation overhead).

**Decision:** a `batch:<id>` label applied at triage. One Claude session per
batch processes all of that batch's issues.

**Hard constraint:** still **one branch + one PR per issue** — never bundle
issues into a single PR. The session is shared; git output stays per-issue.
The orchestrator cuts a branch per issue and drives the agent through them one
at a time within the session.

**Watch:** batch size (2–4 *related* issues — context-window ceiling); and
dependency order (stack branches, or require the earlier issue merged first).

## Gap 2 — Pipeline modes (2-step vs full)

Model the pipeline as explicit stages:

```
implement → validate → PR → review → merge
```

Make the **stop point** configurable (e.g. `AO_STOP_AFTER`):
- `pr` → 2-step: stop at the open PR; a human approves; a second command
  finishes approved PRs.
- `merge` → full pipeline.

The current dry-run flag is a crude precursor — fold it into the stage model.
Add an **auto-review** stage before merge (a review subagent / `/review`).

## Gap 3 — The UI

**Recommendation: local web page first, menu-bar app as phase 2.** The data
already exists as JSON (`runs.jsonl`, `current.json`); a small `serve`
subcommand + a page is fast, cross-platform, and rich. A menu-bar app is more
work and mac-only — build it later as a thin shell over the same data.

Per agent, the UI must show: issue, state (Working / Needs-input / Completed),
token usage, context-window tokens, session id, and a **button that opens the
agent** (a local helper endpoint shelling out `open -a Terminal … claude
--resume <id>`).

**Per-agent token data needs a runner change:** spawn with `--output-format
stream-json` (or `json`) and parse the `usage` block — input/output/cache
tokens, `total_cost_usd` — into each run record.

**Account-level usage metrics:** no official Pro-usage API — aggregate
tokens/cost across `~/<config-dir>/projects/*/*.jsonl` over time windows.

### CodexBar UI reference (the aesthetic to mirror)

A menu-bar dropdown the developer likes for usage metrics:
- Provider tabs across the top: Overview / Claude / Gemini / Antigravity.
- Stacked **sections**, each a label + horizontal progress bar + "% left":
  - **Session** — 88% left, 71% in reserve, resets in 50m
  - **Weekly** — 98% left, 22% in reserve, resets in 5d 8h
  - **Sonnet / Designs / Daily Routines** — 100%-left bars
- **Cost** row — "Today: $13.53 · 13M tokens", "Last 30 days: $2,589.41" (expandable)
- **Subscription Utilization** (expandable)
- Footer: Add Account…, Usage Dashboard, Status Page, Refresh (⌘R), Settings…,
  About, Quit.

Mirror this: clean sectioned rows, each a labeled progress bar with % remaining
+ a reset countdown; cost rollups at the bottom.

## Open decisions

- UI surface — web page vs menu-bar vs both (leaning web-first).
- **Give the tool its own git repo** (e.g. `~/Desktop/Naml/` becomes a
  repo). Right now the prototype is untracked local files with no history.
- **Multi-account / multi-config** — the build is moving to a higher-limit
  work account. `CLAUDE_CONFIG_DIR` and the `claude-personal` assumption must
  be parameterized; do not hardcode an account.
- Stacked PRs vs sequential-merge for dependent batches.
- The per-project config file format (`.toml` / `.json` / `.yaml`).

## Suggested phasing

| Phase | Work | Why first |
|-------|------|-----------|
| 0 | Rename → `agents-orchestrator`; lift out of the Deka repo to `~/Desktop/Naml/`; per-project config file; strip Deka coupling | Makes it the real tool |
| A | Capture per-run token usage (parse `--output-format json`) | Low effort, unlocks UI data |
| B | Batching — `batch:` label + multi-issue session | Cost + context win |
| C | Pipeline stages + configurable stop point + auto-review | Control |
| D | Local web UI cockpit | The "real UI" |
| E | Menu-bar app + CodexBar-style account metrics | Polish |

## Handoff notes

- **Verified `claude` CLI facts:** the `claude agents` TUI only shows agents it
  dispatched itself — scripted `claude -p` sessions are invisible to it and to
  the `/resume` picker; they are reachable only by exact UUID. `--session-id`,
  `--name`, and `--resume` all work with `-p`. Cold `-p` ≈ $0.08 cache cost.
  → The cockpit is `status` + `claude --resume`, not `claude agents`.
- A `claude -p` run is single-shot: Working while the process lives, then a
  terminal state on exit. It never pauses mid-run; you catch agents *between*
  turns. "Resume" spins a fresh process on the same conversation.
- The prototype source is currently at
  `~/Projects/Deka-Press/scripts/afk-orchestrator/` + `~/Projects/Deka-Press/
  Makefile` — untracked (in `.git/info/exclude`). Lift it to `~/Desktop/Naml/`
  as part of Phase 0. A git-history backup also sits on the local branch
  `feat/afk-orchestrator` in that repo until you discard it.
- Nothing of this tool is on any GitHub remote — keep it that way unless you
  give it its own dedicated repo.
