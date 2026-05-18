---
name: setup-naml
description: Bootstrap any project for naml — scaffold docs/ (CONTEXT.md, GLOSSARY.md, adr/ or adrs/, feedback/), .naml/ (config.toml, sprints/), and optionally remove Matt-Pocock-derived skills the project no longer needs. Naml's own skills live globally in ~/.claude/skills/; this skill does NOT copy them per-project. Run once per new project, or to migrate an existing Matt-Pocock-set-up project to naml.
disable-model-invocation: true
---

# Setup Naml

Bootstrap a target project for naml orchestration. Prompt-driven, not a deterministic script: explore, present what you found, confirm with the user before destructive moves, then write.

All naml skills (`setup-naml`, `grill-me`, `compose-sprint`, `note`, `grill-feedback`) are **globally installed** in `~/.claude/skills/`. This skill scaffolds project files only — it does NOT copy skills into the project's `.claude/skills/` directory.

## Pre-flight: verify global naml install

Before doing anything else, check that `~/.claude/skills/` contains all five naml skills:

```bash
ls ~/.claude/skills/ | grep -E "^(setup-naml|grill-me|compose-sprint|note|grill-feedback)$"
```

Expected: 5 lines. If any are missing, stop and tell the user to follow naml's README install instructions (typically a one-line script that symlinks all five into `~/.claude/skills/` from the naml repo's local checkout).

Do not proceed with project setup if the global install is incomplete.

## Process

### 1. Detect current project state

Read whatever exists; don't assume:

- `git remote -v` — is this a GitHub repo? Slug + base branch?
- `pyproject.toml` / `package.json` / `Cargo.toml` / `go.mod` — what kind of project?
- Monorepo signals: `package.json` root with `workspaces` field, OR `turbo.json`, OR `apps/` / `packages/` directories → indicates multiple bounded contexts
- Existing test/lint scripts in `package.json scripts`, `pyproject.toml [tool.poetry.scripts]`, `Makefile` — propose as gate commands
- `CLAUDE.md` / `AGENTS.md` at root — does either exist?
- **Context layout detection** — single-context vs multi-context:
  - `CONTEXT-MAP.md` at root → multi-context (routes to per-app `CONTEXT.md`)
  - `apps/*/CONTEXT.md` or `packages/*/CONTEXT.md` → multi-context (even without a map yet)
  - Only root `CONTEXT.md` (or none) → single-context
- `docs/CONTEXT.md`, `docs/GLOSSARY.md` — already present?
- `docs/adr/` vs `docs/adrs/` — some projects use singular `adr/`. Use whichever exists; default to `adrs/` if neither does
- Per-app `apps/<app>/docs/adr/` directories — preserve, don't touch
- `docs/agents/` — Matt-Pocock-style setup output present (`domain.md`, `issue-tracker.md`, `triage-labels.md`)
  - `domain.md` may contain real project knowledge. It's **renamed to `docs/GLOSSARY.md`** during migration; the other two files are pure MP routing and get deleted (see Section B)
- `.claude/skills/` — list of installed skills, flag any of these MP-derived names: `setup-matt-pocock-skills`, `grill-me`, `grill-with-docs`, `grill-with-mockups`, `to-issues`, `to-prd`, `triage`, `create-backlog`, `create-issue`, `resolve-issue`
- `.agents-orchestrator.toml` — legacy naml v1 config to migrate
- `.naml/` — partial naml setup already present (idempotency check)
- `.scratch/` — Matt-Pocock-style local markdown issue store. If present, check whether it's tracked in git (`git ls-files .scratch/`). Surface to the user:
  - If untracked: offer to add `.scratch/` to `.gitignore` and retire the convention; files stay on disk
  - If tracked (user may have intended local-only but it leaked): offer three options:
    - **Untrack from git, keep on disk** (clean slate; team sees deletions on next pull)
    - **Leave history, gitignore future additions** (least invasive; existing files remain in git)
    - **Defer entirely** (handle later, separate from naml migration)
- `Makefile` — does the naml Makefile drop need installing?

Summarise findings before proceeding.

### 2. Present a migration plan

Present a single consolidated plan with three sections, asking for explicit confirmation on each.

#### Section A — Matt-Pocock skills to remove

List MP-derived skills found in `.claude/skills/`:

- **Replaced by naml** (recommend removing): `setup-matt-pocock-skills`, `grill-me`, `grill-with-docs`, `grill-with-mockups`, `to-issues`, `to-prd`, `triage`, `create-backlog`, `create-issue`, `resolve-issue`
- **Independent value** (recommend keeping unless user disagrees): everything not in the list above — including general-purpose skills (`tdd`, `code-review`, `diagnose`, `rebase`, etc.) and any project-specific skills

Ask the user which to remove. Show the full delete list before executing.

Also flag the MP setup artifacts:
- `docs/agents/` — handling depends on contents (see Section B handling of `domain.md`)
- The `## Agent skills` block in `CLAUDE.md` / `AGENTS.md` if MP set one up — strip and replace with naml's block

#### Section B — Naml structure to create

Show exactly what will be created. **Existing files are never overwritten** — only additions, plus the MP-specific removals from Section A.

**Single-context project:**

```
docs/
  CONTEXT.md               (ADD if missing; preserve if present)
  GLOSSARY.md              (ADD if missing; preserve if present)
  adr/   or   adrs/        (USE whichever exists — do not rename or convert)
    NNNN-naml-orchestration.md   (ADD, using the next available number)
    [existing ADRs untouched]
  feedback/
    inbox.md               (ADD, empty with standard header)
    archive/               (ADD, empty + .gitkeep)
  [all other existing docs/ files LEFT ALONE]
.naml/
  config.toml              (ADD, populated via interview or migration)
  sprints/                 (ADD, empty + .gitkeep)
Makefile                   (ADD if missing — never overwrite existing Makefile)
.gitignore                 (UPDATE: append .naml/sprints/*/state/)
```

**Multi-context project** (`CONTEXT-MAP.md` present OR per-app `CONTEXT.md` found):

```
CONTEXT-MAP.md             (PRESERVE — do NOT modify or move)
apps/<app>/CONTEXT.md      (PRESERVE — per-context glossary)
apps/<app>/docs/adr/       (PRESERVE — per-context ADRs)
docs/
  CONTEXT.md               (DO NOT CREATE — multi-context uses CONTEXT-MAP.md)
  GLOSSARY.md              (ADD via rename if MP domain.md exists; else ASK user
                            whether they want one for cross-cutting terms)
  adr/   or   adrs/        (system-wide ADRs)
    NNNN-naml-orchestration.md   (ADD, next available number)
  feedback/
    inbox.md, archive/     (ADD)
.naml/
  config.toml, sprints/    (ADD — single .naml/ at root; sprints target any app)
```

**Two hard rules:**

- This skill does NOT touch the project's `.claude/skills/` directory except to delete the MP skills confirmed in Section A. Naml's own skills live globally.
- For multi-context layouts: NEVER create a root-level `docs/CONTEXT.md` if `CONTEXT-MAP.md` or per-app `CONTEXT.md` files exist. That would conflict with the existing context routing.

#### Handling `docs/agents/domain.md`

If `docs/agents/domain.md` exists, treat it as **the project's existing glossary in disguise**. Migration:

1. **Move** `docs/agents/domain.md` → `docs/GLOSSARY.md` (preserves all content)
2. **Strip** MP-specific lines:
   - References to `/grill-with-docs` or other MP skills
   - "lazy-create" / "proceed silently if missing" routing instructions
3. **Keep verbatim**:
   - Architecture description (single-context vs multi-context, file layout)
   - Vocabulary discipline rules
   - Cross-context conflict resolution
   - ADR-conflict flagging convention
4. **Add** a header line: `(Migrated from docs/agents/domain.md on YYYY-MM-DD.)` for traceability
5. **Delete** `docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md` (pure MP routing, no project knowledge)
6. **Delete** the now-empty `docs/agents/` directory

If `docs/agents/domain.md` does NOT exist:
- Single-context: interview for a flat term-list `GLOSSARY.md`
- Multi-context: ASK the user whether they want a cross-cutting `GLOSSARY.md`; don't auto-create

#### Section C — Migration of existing config

If `.agents-orchestrator.toml` exists at the project root, propose mapping it to `.naml/config.toml`:

| Old key | New key |
|---|---|
| `[repo].slug` | `[repo].slug` |
| `[repo].base_branch` | `[repo].base_branch` |
| `[[gates]]` | `[[gates]]` |
| `[burst].parallel_lanes` | `[lanes].count` |
| `[pipeline].stop_after` | `[pipeline].stop_after` (default `review`) |
| `[pipeline].auto_review` | `[pipeline].auto_review` (default `true`) |
| `[claude].bin`, `[claude].config_dir` | same |

Old `[burst].max_issues`, `[burst].max_hours`, `[batch].*` are retired — naml v2 doesn't use them. Confirm before discarding.

If `.agents-orchestrator.toml` is absent, run a fresh interview:
- GitHub slug + base branch (auto-detect from `gh repo view` and `git remote`)
- Gate commands (detect from `package.json` / `pyproject.toml` scripts; confirm with user)
- Default lane count (3)
- `stop_after` default (`review`)
- `auto_review` default (`true`)

### 3. Interview for docs/

Only ask for fields that aren't already present. Behaviour differs for single-context vs multi-context.

**Single-context project:**

If `docs/CONTEXT.md` is missing, ask for a 5-line draft:

```markdown
# <Project name>

## What this is
<One paragraph: what it does, primary users.>

## Non-obvious constraints
- <Bullet>

## Out of scope
- <Bullet>
```

If `docs/GLOSSARY.md` is missing, ask for 5-10 core terms:

```markdown
# Glossary

| Term | Meaning |
|---|---|
| <Term> | <Definition> |
```

**Multi-context project:**

- Do not create `docs/CONTEXT.md`. The per-app `CONTEXT.md` files are the source of truth.
- Preserve `CONTEXT-MAP.md` at the root — never modify it.
- If `docs/agents/domain.md` is present: move it to `docs/GLOSSARY.md` per the handling rules above.
- If no `docs/agents/domain.md`: ask the user whether they want a cross-cutting `docs/GLOSSARY.md` for terms that span contexts (tenant, user, environment, etc.). If yes, draft from the user's terms. If no, skip.

**Meta-ADR — auto-generated**:

Write to `docs/adr/NNNN-naml-orchestration.md` (or `docs/adrs/NNNN-...` if using plural), where `NNNN` is the next available number based on what's already in that directory:

```markdown
# ADR NNNN: Adopt naml for agent orchestration

## Status
Accepted — <YYYY-MM-DD>

## Context
This project uses naml to orchestrate Claude Code agents that take sprint
packages, work in parallel lanes, and open PRs for review.

## Decision
Sprint packages live in `.naml/sprints/<sprint-id>/`. Agents read those
packages (not GitHub issues) as their source of truth. Each slice gets a
fresh Claude Code session; review is part of the main pipeline; merge is
tiered.

## Consequences
- Matt-Pocock skill chain (to-issues, triage, batch-ready) retired
- Sprint packages are committed alongside code as reviewable artifacts
- GitHub issues become a projection of the manifest, auto-published at
  sprint kickoff
```

### 4. Confirm and write

Show the user the full planned set of changes — what to delete, what to create, what to migrate — in one summary. Get explicit "yes" before executing.

Execution order:

1. **Removals first**, with one final confirmation per directory:
   - Move agreed Matt-Pocock skill dirs from `.claude/skills/` to a `/tmp/` trash (so the user can recover if needed)
   - Handle `docs/agents/` per the rules above (rename or delete)
   - Strip the `## Agent skills` block from `CLAUDE.md` / `AGENTS.md` (only the MP-installed one; leave everything else)

2. **Scaffold naml structure**:
   - Write `docs/CONTEXT.md`, `docs/GLOSSARY.md` if applicable (per layout rules)
   - Write the meta-ADR at the next available number
   - Create `docs/feedback/inbox.md` with the standard header (`# Feedback inbox\n\n`)
   - Create `docs/feedback/archive/.gitkeep`
   - Create `.naml/config.toml` (migrate from `.agents-orchestrator.toml` if present)
   - Create `.naml/sprints/.gitkeep`

3. **Update `.gitignore`** — append if not already excluded:
   ```
   # naml runtime state
   .naml/sprints/*/state/
   ```

4. **Drop the Makefile** if not present (copy from naml's `templates/Makefile`).

5. **Add naml block to `CLAUDE.md` or `AGENTS.md`** at project root:

```markdown
## Naml skills

This project uses naml for agent orchestration. Sprint packages live in
`.naml/sprints/<id>/`. To start a new sprint:

1. `/grill-me` — multi-phase grilling (intent, architecture, design, slicing)
2. `/compose-sprint` — in the same session, materialise the sprint package
3. `[Run Sprint]` button (or `make burst`) — orchestrator picks up

For post-ship iteration:
- `/note <complaint>` — append a bullet to `docs/feedback/inbox.md`
- `/grill-feedback` — short focused grilling against the parent sprint

Project conventions (paths adapt to single-context vs multi-context layout):

- **Project glossary + navigation**: `docs/GLOSSARY.md` (always present after
  setup). In single-context projects, a flat term list. In multi-context
  monorepos, also describes the context layout and routes to per-app
  `CONTEXT.md` files. Grilling reads this first.
- **Project intent**: `docs/CONTEXT.md` (single-context only) or per-app
  `apps/<app>/CONTEXT.md` (multi-context). Grilling reads only the
  contexts relevant to the sprint being grilled.
- **Architectural decisions**: `docs/adr/` (or `docs/adrs/`) at root for
  system-wide; `apps/<app>/docs/adr/` for context-specific (multi-context only)
- **Feedback inbox**: `docs/feedback/inbox.md`, managed via `/note`
- **Sprint packages**: `.naml/sprints/<id>/` — manifest, overview, slices, artifacts

All naml skills (`grill-me`, `compose-sprint`, `note`, `grill-feedback`) are
installed globally in `~/.claude/skills/` and work in any project. The
project's own `.claude/skills/` directory is reserved for project-specific
skills.
```

6. **Stage everything as a single git commit** (don't push):
   ```
   chore: migrate to naml architecture

   - Remove Matt-Pocock setup artifacts (docs/agents/, MP skills, CLAUDE.md block)
   - Scaffold docs/{GLOSSARY.md,feedback/} (+ CONTEXT.md if single-context)
   - Scaffold .naml/{config.toml,sprints/}
   - Add ADR documenting naml adoption
   ```

7. **Print a "next steps" message** for the user:

```
Setup complete. To start your first sprint:

  /grill-me

This will grill you through intent → architecture → design → slicing.
When the grill converges, run /compose-sprint in the same chat to write
the package to .naml/sprints/<id>/.
```

## Safety rules

- **Never delete files without confirmation.** Group removals into a single confirmation prompt before any destructive operation. List every path that will be removed before executing. Prefer `mv` to a `/tmp/` trash location over `rm -rf` so the user can recover.
- **Never touch `docs/` files other than the ones this skill explicitly creates.** The project's existing documentation (architecture docs, ADRs, runbooks, etc.) is never modified or deleted. Only `docs/agents/` (MP routing config) is a candidate for removal, and only with explicit confirmation.
- **Never overwrite `docs/CONTEXT.md` or `docs/GLOSSARY.md` if they exist.** Skip with a note instead.
- **Never modify `.agents-orchestrator.toml` in place.** Read it, write the new `.naml/config.toml`, leave the old file for the user to remove manually.
- **Adapt to existing `docs/adr/` (singular) — don't force `adrs/`.** Use the next available ADR number based on what's already in the directory.
- **Don't touch the project's `.claude/skills/` directory** except to delete the explicit MP list. Anything not in the recognised MP list is presumed project-specific and left alone.
- **Commit but never push.** The user reviews and pushes manually.
