# agents-orchestrator — operating guide

`agents-orchestrator` is a **personal developer tool**. It runs on your own
machine to speed up your own work — it is not a product, not a service, and
nothing about it runs on anyone else's machine.

It turns triaged GitHub issues into reviewed, merged PRs without you at the
keyboard. It picks up issues labelled `ready-for-agent`, drives a Claude
session against each, validates locally, and merges when every gate is green.

There is **zero GitHub Actions usage** — all validation runs on your local
machine.

## How it works

### The label state machine

```
needs-triage ──(human triages)──► ready-for-agent
                                        │  orchestrator picks oldest
                                        ▼
                                  agent-running
                                        │
                        ┌───────────────┴───────────────┐
                   gates pass                       gates fail
                        │                                │
                        ▼                                ▼
                   agent-done                       agent-failed
              (PR opened / merged)            (branch + PR left for a human)
```

`needs-info` is applied automatically when the agent produces no diff
(usually an unclear issue), or by you when an issue is too vague to action.

Label names are configurable in `.agents-orchestrator.toml` under `[labels]`.

### The run cycle

For `slow` *and* `burst`, the inner loop is identical:

1. **Headroom check** — estimate the Claude Pro 5h window. Slow mode bails
   only when the window is provably depleted; burst mode stops when fewer
   than `burst.min_headroom` (default 8) messages remain.
2. **Pick** the oldest open `ready-for-agent` issue.
3. **Re-label** it `agent-running` and cut `agent/issue-<n>-<slug>` from the
   base branch.
4. **Run** the agent — a Claude session with a freshly generated UUID
   (`--session-id`) and a display name (`AO #<n>: <title>`), headless
   against the branch, killed at the `run.cap_minutes` cap (default 30).
5. **Validate** — run every gate in `[[gates]]` order. First failure stops
   the chain.
6. **Auto-retry** — if a gate fails, the agent's *own session* is resumed
   (`claude --resume <uuid>`) with the gate's error output, up to
   `run.max_retries` (default 2) times, re-validating after each. Warm
   cache, full context — this is how a near-miss (a stray type error, a
   missed import) self-heals.
7. **Inspect** — the branch must have a non-empty diff vs the base and must
   not conflict with it. An empty diff routes the issue to `needs-info`.
8. **Green path** — push the branch, open a PR, label the issue
   `agent-done`. If not in dry-run and there is no conflict,
   `gh pr merge --squash --delete-branch`.
9. **Failure path** — label `agent-failed`, push the branch + open a draft
   PR, comment a log excerpt + the `claude --resume` line, leave it for a
   human.
10. **Always** — append a one-line summary (with the session UUID) to
    `runs.jsonl`.

Every run is a real, named, resumable session. `make status` prints a
`claude --resume <uuid>` line for each — that is how you peek inside a
running agent or take one over by hand.

### Two modes

- **slow** (launchd-driven, unattended): one issue per invocation, then exit.
- **burst** (manual — "clear the queue"): loops the inner cycle until *any*
  cap trips: queue empty, Pro headroom below `burst.min_headroom`,
  `burst.max_hours` (4) elapsed, or `burst.max_issues` (5) processed.

## Driving it — `make`

Run everything from the target repo's root with `make`:

```bash
make            # supervised burst — clear the queue now, you watch (default)
make dry-burst  # same, but never merges — use during a trial
make slow       # one issue, then exit
make status     # the cockpit
make ao-on      # enable the unattended hourly loop (launchd)
make ao-off     # disable it
make ao-status  # is the loop loaded?
make help       # list targets
```

## The cockpit

`make status` is your live view. It shows the queue, the currently running
agent (with elapsed time and cap remaining), the Pro window, and recent
runs — each with a paste-ready `claude --resume <uuid>` line.

To peek inside an agent or take it over, paste that line. If you've
exported `CLAUDE_CONFIG_DIR` (for a non-default account), it shows up in
the hint:

```bash
claude --resume <uuid>
# or, if you're using a non-default account:
CLAUDE_CONFIG_DIR=~/.claude-personal claude --resume <uuid>
```

That opens the agent's session as a normal interactive chat — full history,
and you can type new instructions. Note: orchestrator-spawned headless
sessions do **not** appear in the built-in `claude agents` TUI (that view
only tracks agents dispatched from within itself); `make status` +
`claude --resume` is the cockpit.

## Unattended on / off

The hourly launchd loop is **opt-in** and stays off until you enable it.

```bash
make ao-on      # install + load the launchd agent — runs hourly, slow mode
make ao-off     # unload it — stops the hourly trigger
make ao-status  # show whether it is loaded
```

`make ao-off` does not touch any branches, PRs, or labels — it only stops
new runs from starting. In-flight runs finish on their own.

## Flipping dry-run off

The first window with `AO_DRY_RUN=1` set in the plist. In dry-run the
orchestrator does the **entire** flow — branch, agent, validation, PR,
labels, logs — but **does not merge**. PRs pile up for you to eyeball.

Once a window of dry-runs looks clean:

```bash
launchctl unload ~/Library/LaunchAgents/<your-plist-name>
# Edit the plist:
#   delete   <key>AO_DRY_RUN</key>
#   and the next line <string>1</string>
launchctl load ~/Library/LaunchAgents/<your-plist-name>
```

From the next run on, green PRs auto-merge.

## Reading logs

| Path | Contents |
|------|----------|
| `~/Library/Logs/agents-orchestrator/<slug>/<run-id>.log` | full per-run log: agent output + every gate |
| `~/Library/Logs/agents-orchestrator/<slug>/runs.jsonl` | one JSON line per run (the ledger) |
| `~/Library/Logs/agents-orchestrator/<slug>/current.json` | present only while a run is in flight |
| `~/Library/Logs/agents-orchestrator/launchd.log` | stdout/stderr of every launchd trigger |

`<slug>` defaults to the repo slug with `/` → `-`. Override per-project
via `[logs] dir` in `.agents-orchestrator.toml`.

## Handling a failed run

A run is marked `agent-failed` only **after** the auto-retry budget
(`run.max_retries`, default 2) is exhausted — so a failure here means the
agent could not self-correct across three attempts. The issue gets an
`agent-failed` label, a draft PR, and a comment naming the gate, the log
path, and a `claude --resume <uuid>` line. To recover:

1. Run `claude --resume <uuid>` (from the comment or `make status`) — that
   opens the agent's session, so you can see what it tried and instruct
   it directly. Or open the run log and scroll to the failing `GATE:`
   header.
2. The branch `agent/issue-<n>-<slug>` was pushed with a draft PR — fix
   it by hand there, or re-scope the issue.
3. When fixed, merge the PR yourself, or re-label the issue
   `ready-for-agent` to let the orchestrator try again from a fresh
   branch + session.
4. If the issue was simply too vague, re-label it `needs-info` and add
   detail.

Common failure causes:

- **`run-cap`** — the agent hit the wall-clock cap. The issue is probably
  too big; split it.
- **A specific gate** (`test` / `typecheck` / `lint` / `build` / …) — the
  change still broke a gate after every retry. The log tail in the issue
  comment shows what.
- **`agent`** — `claude` itself exited non-zero (e.g. auth expired).

A `needs-info` outcome (not a failure) means the agent ran but produced
**no code change** — usually the issue is unclear. Sharpen it and re-label
`ready-for-agent`.

## Picking a Claude account

`agents-orchestrator` does not force `CLAUDE_CONFIG_DIR`. It inherits the
current shell's value, so switching accounts is a one-line shell change:

```bash
# Default / work account (claude's own default):
unset CLAUDE_CONFIG_DIR
make burst

# Personal / aliased account:
export CLAUDE_CONFIG_DIR=~/.claude-personal
make burst
```

For unattended runs, set (or omit) `CLAUDE_CONFIG_DIR` inside the launchd
plist's `EnvironmentVariables`.

You can also pin an account per-project via `[claude] config_dir = ...`
in `.agents-orchestrator.toml` — that path is used for reading Pro-window
transcripts. The spawned `claude` subprocess still picks up the env from
the shell, so for spawning, set the env var; for transcript reads only,
the TOML pin works.

## Known limitations of Pro-window estimation

The Claude Pro plan meters ~45 messages per rolling 5h window, but there is
no API to read current usage. `claude_session.py` estimates it by walking
local session transcripts under `CLAUDE_CONFIG_DIR/projects/` and counting
genuine *user prompts* in the last 5 hours. Caveats:

- **It is a proxy, not a meter.** One `claude -p` invocation logs roughly
  one prompt, but the mapping to plan-metered "messages" is approximate.
- **It only sees local transcripts.** Messages sent from another machine,
  or the claude.ai web app, under the same account are invisible to it.
- **It can over- or under-count.** Treat the dashboard's headroom number
  as a rough gauge, not gospel.

This is deliberately best-effort. The real safety net is the per-run
wall-clock cap plus slow mode's one-issue-per-invocation ceiling — even if
the estimator is wrong, the orchestrator cannot run away. Burst mode is
the only mode that hard-trusts the estimate, which is why it refuses to
start on an unknown.

## Configuration reference

Per-project file: `.agents-orchestrator.toml` at the target repo root.
See [`templates/.agents-orchestrator.toml.example`](./templates/.agents-orchestrator.toml.example)
for the full schema.

Cross-cutting env-var overrides (all optional):

| Variable | Default | Meaning |
|----------|---------|---------|
| `CLAUDE_CONFIG_DIR` | unset → `~/.claude` | Claude account dir (inherited by spawned `claude`) |
| `AO_DRY_RUN` | unset | `1` = do everything except merge |
| `AO_PRO_LIMIT` | 45 | Pro message cap per 5h window |
| `AO_RUN_CAP_MINUTES` | 30 | Per-run wall-clock cap |
| `AO_MAX_RETRIES` | 2 | Resume-and-retry attempts on a failed gate |
| `AO_BURST_MAX_ISSUES` | 5 | Burst-mode issue cap |
| `AO_BURST_MAX_HOURS` | 4 | Burst-mode wall-clock cap |
| `AO_BURST_MIN_HEADROOM` | 8 | Min Pro messages free before next burst run |
| `AO_REPO_ROOT` | TOML `repo.root` | Override the target repo working copy |
| `AO_LOG_DIR` | TOML `logs.dir` | Override the log directory |
| `AO_CLAUDE_BIN` | `claude` | Path to the `claude` binary |
