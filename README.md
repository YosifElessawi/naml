# Naml — نمل

> A colony of Claude Code agents that picks up triaged GitHub issues, drives a
> session against each, validates locally, and ships PRs while you sleep.

**Naml** (نمل, Arabic for *ants*) is a personal-scale orchestrator for
[Claude Code](https://claude.com/claude-code). Like ants on a queue, it
fans many small agents across your backlog — each one owns one issue, opens
one PR, and only merges when every local gate is green.

Python 3.11+, stdlib only. **Zero GitHub Actions minutes** — every gate runs
on your machine.

---

## How it fits

Naml owns the **implementation phase** of a spec-driven workflow:

```
grilling → PRD → to-issues → triage  ⇒  Naml  ⇒  merged PRs
```

Issue authoring + triage happens elsewhere (Matt Pocock's skills, for
example). Naml picks up whatever those produce — issues labelled
`ready-for-agent` — and drives them to merged PRs.

## Setup

### Clone it

```bash
git clone https://github.com/YosifElessawi/naml.git ~/Desktop/Claudey
```

(Any path works; the templates default to `~/Desktop/Claudey` but you can
point `AO_HOME` at wherever you cloned it.)

### Per target repo

Drop two files at the repo's root:

```bash
cd ~/Projects/your-repo
cp ~/Desktop/Claudey/templates/Makefile .
cp ~/Desktop/Claudey/templates/.agents-orchestrator.toml.example .agents-orchestrator.toml
```

Then edit `.agents-orchestrator.toml`:

- Set `repo.slug = "owner/your-repo"`
- Set `repo.base_branch` (e.g. `main` / `master`)
- Replace the `[[gates]]` blocks with whatever commands validate this repo
  (pnpm, uv, cargo, make — anything that exits non-zero on failure)

Optional — drop the issue template and launchd plist if you want them:

```bash
mkdir -p .github/ISSUE_TEMPLATE
cp ~/Desktop/Claudey/templates/issue-template.yml .github/ISSUE_TEMPLATE/agent-task.yml
cp ~/Desktop/Claudey/templates/launchd.plist.example com.naml.your-repo.plist
# edit the plist's TODO placeholders
```

### Bootstrap the label state machine (one-time per repo)

```bash
gh label create ready-for-agent --color "0E8A16" --description "Naml will pick this up"
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
make slow       # process one issue/batch, then exit
make status     # the terminal cockpit: queue, running agent, recent runs + session ids
make web        # the web cockpit: http://127.0.0.1:7777
make finish     # merge PRs left open by an earlier stop_after run
make ao-on      # enable the hourly unattended loop (launchd)
make ao-off     # disable it
make ao-status  # is the loop loaded?
make help       # list every target
```

## Pipeline stages

The pipeline runs `implement → validate → PR → review → merge`. Pick where
to stop via `[pipeline] stop_after` in the TOML (or `AO_STOP_AFTER=...`):

- **`merge`** (default) — full pipeline, green PRs auto-merge.
- **`review`** — stop with PR open + auto-review posted as a PR comment.
  A human reviews the review, then runs `make finish` to merge.
- **`pr`** — stop with PR open (the old `dry_run` behaviour).

`auto_review = true` (or `AO_AUTO_REVIEW=1`) runs the review BEFORE merge
even when `stop_after = "merge"` — useful as a safety net.

## Batching

Issues whose labels start with `batch:<id>` (e.g. `batch:auth-flow`) are
grouped into one Claude session. Each issue still gets its own branch and
its own PR — only the session is shared, for warm cache + context. Cap
the size in the TOML's `[batch] max_size` (default 4 — keep it small).

### Auto-batching after triage — `/batch-ready`

Triage produces a queue of `ready-for-agent` issues with no batch labels.
The companion Claude Code skill `/batch-ready` (installed at
`~/.claude/skills/batch-ready/`) reads the queue, clusters related issues,
proposes batches, and — after you confirm — applies `batch:<id>` labels
via `gh`. Run from anywhere inside the target repo:

```
/batch-ready              # cluster + propose + apply
/batch-ready --dry-run    # propose only, no labels applied
```

## Picking a Claude account

Naml inherits `CLAUDE_CONFIG_DIR` from the shell that runs it.

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

## About the name

**Naml** (نمل) is Arabic for *ants*. The metaphor: many small agents
working in coordinated parallel on a shared queue — each one carrying a
crumb (an issue) back to the colony (the merged main branch). Short to
type, distinctive, and the swarm imagery captures what the tool does.

> **Note:** The Python package directory is still named `agents_orchestrator/`,
> and the per-project config file is still `.agents-orchestrator.toml` — the
> internal rename will land in a follow-up to avoid breaking existing
> installs. The CLI surface (`make ...`) stays compatible across the rename.

## Status

Naml is a **personal developer tool** I built for my own workflow and am
releasing as open source so friends and curious people can use it or
hack on it. The roadmap (see [`roadmap.md`](./roadmap.md)) reflects what
I personally need next — not a product backlog. Issues and PRs welcome,
but expect slow response times and opinionated reviews.

## Further reading

- [`OPERATING.md`](./OPERATING.md) — operating guide: label state machine,
  failure recovery, Pro-window caveats.
- [`roadmap.md`](./roadmap.md) — forward plan (batching, pipeline stages,
  web UI, etc.).

## License

MIT — see [`LICENSE`](./LICENSE).
