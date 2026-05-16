# agents-orchestrator

A personal dev tool. Picks up triaged GitHub issues, drives a Claude session
against each, runs local validation gates, opens a PR, and merges when
everything is green. Project-agnostic — configure once per target repo.

Python 3.11+, stdlib only. No CI minutes — every gate runs on your machine.

## How it fits

This tool owns the **implementation phase** of a spec-driven workflow:

```
grilling → PRD → to-issues → triage  ⇒  agents-orchestrator  ⇒  merged PRs
```

Issue authoring + triage happens elsewhere (Matt Pocock's skills, for
example). This tool picks up whatever those produce — issues labelled
`ready-for-agent` — and drives them to merged PRs.

## Setup

### One-time (per machine)

```bash
# Already done if you're reading this from ~/Desktop/Claudey/.
ls ~/Desktop/Claudey   # confirm the tool is here
```

### Per target repo

Drop two files at the repo's root:

```bash
cd ~/Projects/your-repo
cp ~/Desktop/Claudey/templates/Makefile .
cp ~/Desktop/Claudey/templates/.agents-orchestrator.toml.example .agents-orchestrator.toml
```

Then edit `.agents-orchestrator.toml`:

- Set `repo.slug = "owner/your-repo"`
- Set `repo.base_branch` (e.g. `master` / `main`)
- Replace the `[[gates]]` blocks with whatever commands validate this repo
  (pnpm, uv, cargo, make — anything that exits non-zero on failure)

Optional — drop the issue template and launchd plist if you want them:

```bash
mkdir -p .github/ISSUE_TEMPLATE
cp ~/Desktop/Claudey/templates/issue-template.yml .github/ISSUE_TEMPLATE/agent-task.yml
cp ~/Desktop/Claudey/templates/launchd.plist.example com.agents-orchestrator.your-repo.plist
# edit the plist's TODO placeholders
```

### Bootstrap the label state machine (one-time per repo)

```bash
gh label create ready-for-agent --color "0E8A16" --description "Orchestrator will pick this up"
gh label create agent-running   --color "FBCA04" --description "Currently being worked on by an agent"
gh label create agent-done      --color "5319E7" --description "Agent finished, PR opened/merged"
gh label create agent-failed    --color "B60205" --description "Agent run failed validation"
gh label create needs-info      --color "C5DEF5" --description "Blocked on missing info"
```

## Driving it — `make`

From the target repo's root:

```bash
make            # supervised burst — clear the queue now, you watch (default)
make dry-burst  # same, but never merges — use during a trial
make slow       # process one issue, then exit
make status     # the cockpit: queue, running agent, recent runs + session ids
make ao-on      # enable the hourly unattended loop (launchd)
make ao-off     # disable it
make ao-status  # is the loop loaded?
make help       # list every target
```

## Picking a Claude account

The orchestrator inherits `CLAUDE_CONFIG_DIR` from the shell that runs it.

- **Default** (unset) → `claude` uses its own default: `~/.claude`.
- **Personal** (or any aliased account) → `export CLAUDE_CONFIG_DIR=~/.claude-personal`
  before `make`.

You can also pin an account per-project by setting `claude.config_dir` in
`.agents-orchestrator.toml`. The shell env wins over the default but does
not override a TOML value used for transcript reads.

## What you get back

Per run, in `~/Library/Logs/agents-orchestrator/<slug>/`:

| File | Contents |
|------|----------|
| `<run-id>.log` | full per-run log: agent output + every gate's output |
| `runs.jsonl` | one JSON line per run (the ledger) |
| `current.json` | present only while a run is in flight |

`make status` prints a `claude --resume <uuid>` line for every run — paste
it to peek inside an agent or take it over by hand.

## Further reading

- [`OPERATING.md`](./OPERATING.md) — operating guide: label state machine,
  failure recovery, Pro-window caveats.
- [`roadmap.md`](./roadmap.md) — forward plan (batching, pipeline stages,
  web UI, etc.).
