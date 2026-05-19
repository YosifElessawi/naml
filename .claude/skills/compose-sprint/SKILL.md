---
name: compose-sprint
description: Materialise a grill session into a sprint package on disk. Runs in the same Claude session as the preceding /grill-me (no handoff). Writes .naml/sprints/<id>/{manifest.toml, overview.md, slices/*.md, artifacts/}, runs pre-flight checks, stages a git commit. Almost entirely deterministic — only LLM step is touches: glob inference.
disable-model-invocation: true
---

# Compose Sprint

Take the grill output that's already in the conversation context and turn it into a canonical sprint package.

This skill runs in the **same Claude session** as `/grill-me`. There is no file-based handoff between them — the grill content lives in the conversation, and this skill structures it.

## Process

### 1. Confirm the grill content is here

If the conversation doesn't contain a recent `/grill-me` session, stop and ask the user to run `/grill-me` first. Don't try to grill from scratch.

If the grill targets an existing parent sprint (`/grill-feedback` flow), capture the parent sprint ID.

### 2. Generate sprint ID

Format: `YYYY-MM-DD-<kebab-slug-of-title>`.

Examples:
- `2026-05-18-html-renderer`
- `2026-05-22-html-renderer-fb1` (if `--feedback` of a previous sprint and the title is the parent's)

Confirm the ID with the user. They can override.

### 3. Write the package files

Create `.naml/sprints/<id>/` and write:

**`manifest.toml`** (from grill content):

```toml
[sprint]
id            = "<id>"
title         = "<title>"
target_repo   = "<from .naml/config.toml or git remote>"
base_branch   = "<from .naml/config.toml>"

[meta]
parent_sprint = "<parent id or empty>"
kind          = "greenfield"  # or "feedback"

[[slices]]
id         = "slice-1"
title      = "<title>"
type       = "AFK"            # or HITL
depends_on = []
touches    = ["<globs>"]
prompt     = "slices/slice-1.md"
adrs       = []               # optional list of docs/adrs/*.md paths

# ... one [[slices]] block per slice
```

**`overview.md`** (from grill Phase 1):

```markdown
# Sprint: <title>

## Goal
<from grill>

## Why now
<from grill>

## Non-obvious constraints
- <bullets>

## Out of scope
- <bullets>
```

**`slices/slice-N.md`** for each slice:

```markdown
# slice-N: <title>

## What to build
<end-to-end behavior — pulled from grill Phase 4>

## Acceptance criteria
- [ ] <from grill Phase 5>

## Artifacts (referenced, not embedded)
- <relative paths or "none">

## Constraints
<slice-specific constraints — empty if none>

## Notes from grilling session
<verbatim nuance worth preserving — empty if none>
```

**`artifacts/`** — copy any files the user mentioned during grill Phase 6 (mockups, eval data, samples).

**`feedback.md`** — only if this is a feedback sprint. Write the consumed inbox bullets verbatim.

### 4. Infer touches: globs (the only LLM judgment step)

If grill-me didn't capture exact `touches` globs, infer them now:

For each slice:
- Read `slices/slice-N.md`
- Scan the codebase for files that match the slice's described scope (use `Glob` and `Grep`)
- Propose globs that cover the likely write surface
- Be liberal: better to over-declare and serialize than under-declare and conflict

Present the inferred globs back to the user before finalizing the manifest. They can edit.

### 5. Pre-flight checks (deterministic)

Run these checks; refuse to finalize if any fail:

**a) DAG validation**
- Every `depends_on` ID must be a real slice in the manifest
- No cycles (do a topological sort; if it doesn't terminate, report the cycle)

**b) Touches overlap check**
- For any two slices with `depends_on` not containing each other:
  - Compare `touches` globs
  - If they overlap, add a synthetic serial edge with a warning to the user
  - Surface: "slice-A and slice-B both touch <path> — declaring slice-A blocks slice-B to be safe. Override if you know they're disjoint."

**c) Required field check**
- Every slice has: `id`, `title`, `type`, `depends_on`, `touches`, `prompt`
- `type` is `AFK` or `HITL`
- `prompt` file exists at the declared path

### 6. Archive feedback inbox (only for feedback sprints)

If this is a feedback sprint:

- Move `docs/feedback/inbox.md` → `docs/feedback/archive/<sprint-id>.md`
- Create a fresh empty `docs/feedback/inbox.md` with the standard header:

```markdown
# Feedback inbox
```

### 7. Stage a git commit

Don't push. Stage:

```
.naml/sprints/<id>/
docs/feedback/inbox.md            (if feedback sprint)
docs/feedback/archive/<id>.md     (if feedback sprint)
docs/adrs/NNNN-<title>.md         (if any new ADRs were drafted during grill)
```

Commit message:

```
sprint: <title>

Slices: N (M AFK, K HITL)
Parent: <parent-id or "none — greenfield">
```

### 8. Print next steps

```
Sprint package staged at .naml/sprints/<id>/.

To execute:
  Press [Run Sprint] in the cockpit, or `make burst`.

To inspect:
  Open .naml/sprints/<id>/manifest.toml — that's the DAG.
  Open .naml/sprints/<id>/slices/slice-N.md — those are the agent prompts.
```

## Safety rules

- **Don't push.** Stage and commit only.
- **Don't modify existing sprints.** This skill writes a new sprint dir; if `<id>` already exists, ask the user for a different ID.
- **Don't edit docs/adrs/ unless the grill drafted new ADRs.** Even then, ask before writing.
- **Don't archive the inbox if no feedback bullets were consumed.** A failed grill-feedback should leave the inbox intact.
- **Never invent slices.** If the grill is unclear about a slice, ask the user before writing it.

## Failure modes to surface clearly

- **Empty grill** — no slices in conversation context → tell the user to run `/grill-me` first
- **Cyclic DAG** — surface the cycle, point at which slices form it, suggest where to break it
- **Touches overlap that the user explicitly said was independent** — show the conflict, default to serializing with a warning, let the user override
- **Sprint ID collision** — refuse to overwrite; ask for a new ID
- **Missing project setup** — if `.naml/config.toml` is missing, tell the user to run `/setup-naml` first
