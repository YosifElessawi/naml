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
make slow       # one issue/batch, then exit
make status     # the terminal cockpit
make web        # the web cockpit — http://127.0.0.1:7777
make finish     # merge PRs left open by an earlier stop_after run
make ao-on      # enable the unattended hourly loop (launchd)
make ao-off     # disable it
make ao-status  # is the loop loaded?
make help       # list targets
```

## Pipeline stages

```
implement → validate → PR → review → merge
```

`stop_after` (TOML `[pipeline] stop_after` or env `AO_STOP_AFTER`) names
where the pipeline halts. `pr` stops with the PR open. `review` adds an
auto-review comment and stops. `merge` (default) runs end-to-end.

`auto_review = true` (or `AO_AUTO_REVIEW=1`) runs the review before merge
even when `stop_after = "merge"`. The review runs in a FRESH Claude session
(not the implementing agent's), reads the diff via `gh pr diff`, and posts
a plain-English comment with a `VERDICT: LGTM` or `VERDICT: REQUEST_CHANGES`
trailer. The verdict is informational today — it does not gate the merge.

Use `make finish` to merge PRs that an earlier `stop_after={pr,review}` run
left open. It finds issues at `agent-done` with an open PR and merges any
that are not in conflict.

## Batching

A `batch:<id>` label (default prefix configurable in `[batch] label_prefix`)
groups multiple issues into one Claude session. Constraints:

- **One branch + one PR per issue.** The session is shared; git output is
  not. Each issue still gets its own `agent/issue-N-slug` branch and its
  own PR.
- **Hard cap on size** (`[batch] max_size`, default 4). The Claude context
  window is the real limit — batches of 2–4 *related* issues work well.
- **Bottom-out on first failure.** If the first issue of a batch fails,
  the rest of the batch is skipped — the shared session is suspect and
  poisoning subsequent issues is worse than re-queueing them.

If you have **dependent** batched issues (issue B builds on issue A) and
you want them stacked: that's not the default. The current batch model
treats issues as co-equal (all branches cut from the same base). Stacked
PRs require separate work — see roadmap.

⚠️ When stacked PRs eventually land: never delete a base branch with open
dependent PRs. Either retarget dependents to `master` before delete, or
merge bottom-up rebasing each branch first. Deleting a base branch closes
its dependent PRs.

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
| `AO_DRY_RUN` | unset | Shorthand for `AO_STOP_AFTER=pr` |
| `AO_STOP_AFTER` | `merge` | `pr` / `review` / `merge` |
| `AO_AUTO_REVIEW` | unset | `1` = run auto-review before merge |
| `AO_PRO_LIMIT` | 45 | Pro message cap per 5h window |
| `AO_RUN_CAP_MINUTES` | 30 | Per-run wall-clock cap |
| `AO_MAX_RETRIES` | 2 | Resume-and-retry attempts on a failed gate |
| `AO_BURST_MAX_ISSUES` | 5 | Burst-mode issue cap |
| `AO_BURST_MAX_HOURS` | 4 | Burst-mode wall-clock cap |
| `AO_BURST_MIN_HEADROOM` | 8 | Min Pro messages free before next burst run |
| `AO_BATCH_PREFIX` | `batch:` | Issue-label prefix that groups a batch |
| `AO_BATCH_MAX_SIZE` | 4 | Max issues in one batched session |
| `AO_REPO_ROOT` | TOML `repo.root` | Override the target repo working copy |
| `AO_LOG_DIR` | TOML `logs.dir` | Override the log directory |
| `AO_CLAUDE_BIN` | `claude` | Path to the `claude` binary |
