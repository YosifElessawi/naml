---
name: grill-feedback
description: Short focused grilling against a previously-merged parent sprint, driven by docs/feedback/inbox.md. Pre-loads curated parent context (overview, slice summaries, reviews, ADRs). Output: typically 1-3 slices. Hand off to /compose-sprint in the same session.
disable-model-invocation: true
---

# Grill Feedback

Differential grilling — refines an already-shipped sprint based on real-world feedback.

This is `/grill-me` with three differences:
1. **Source is `docs/feedback/inbox.md`**, not a fresh idea
2. **Curated context pre-load** from the parent sprint (not a full re-read)
3. **Shorter** — typically 1-3 slices, intent + slicing + AC only

## Process

### 1. Resolve target sprint

Read `docs/feedback/inbox.md`. Group the bullets by their `target:` heading.

If all bullets share one target: use that as the parent sprint.

If bullets span multiple parents: propose splitting into multiple feedback sprints, one per parent. Ask the user to pick which to grill now.

If the user passed `--sprint=<id>` as an argument, override and use that.

If inbox is empty: ask the user to paste feedback inline, and target the most recently merged sprint by default.

### 2. Group bullets by area

Within the chosen parent sprint, scan the bullets for thematic grouping:

- Bullets mentioning the same slice or component → one group
- Bullets touching the same files / surfaces → one group
- Mix of trivial fixes (typo, label) + substantial (redesign) → split

Propose a grouping to the user:

```
I see 5 bullets in the inbox targeting html-renderer:
  Group A (3 bullets): slice-2 renderer behavior
  Group B (1 bullet):  slice-3 eval UI
  Group C (1 bullet):  new area — CLI input handling

Proposal:
  - Feedback sprint 1: Group A → 2 slices  (target: html-renderer)
  - Feedback sprint 2: Group B → 1 slice   (target: html-renderer)
  - Group C is a new area — recommend running /grill-me as a greenfield sprint instead

Proceed with sprint 1?
```

Always let the user override the grouping.

### 3. Load curated parent context

For the chosen parent sprint, read into conversation context **only** what's relevant:

- `docs/CONTEXT.md` — always
- `docs/adrs/` — always (constraints carry forward)
- `.naml/sprints/<parent-id>/overview.md` — always
- For each slice mentioned in the feedback bullets:
  - `.naml/sprints/<parent-id>/slices/<slice-id>.md`
  - `.naml/sprints/<parent-id>/state/<slice-id>.summary.md` (what got built)
  - `.naml/sprints/<parent-id>/state/<slice-id>.review.md` (what auto-review caught)
  - Optionally: `git show <merge-commit>:<file>` for files the slice touched

**Don't dump all parent context.** A 20-slice parent sprint should pre-load only the 2-3 slices mentioned in the feedback.

### 4. Classify the feedback

Short questions to the user:

- **Kind**: bug fix, missed-case enhancement, partial redesign?
- **ADR impact**: does this feedback challenge an ADR from the parent sprint? If yes, draft an updated/superseding ADR for compose-sprint to write.
- **Scope check**: is the feedback consistent with the parent's `out of scope` list? Flag if it's pulling deferred work back in.

### 5. Vertical slicing (typically 1-3 slices)

Same vertical-slice discipline as `/grill-me` Phase 4, but tighter. For each slice:

- **Title** — references the parent slice when relevant ("Fix slider snap behavior in HTML renderer")
- **Type** — AFK / HITL
- **`depends_on`** — IDs within this feedback sprint (most feedback sprints have no internal deps; slices are independent fixes)
- **`touches`** — globs; lean on the parent's slice summaries to know where the relevant code lives

### 6. Acceptance criteria

For each slice, 2-4 testable criteria that directly address the feedback bullet(s) the slice covers.

Cross-reference: every consumed feedback bullet should map to at least one slice's acceptance criterion. If a bullet has no matching criterion, surface it ("Bullet 'API missing created_at' isn't covered by any slice — drop it or add a slice?").

### 7. Wrap

Summarise:

```
Feedback sprint draft ready:
  Parent: <parent-id>
  Title: <derived from grouped bullets>
  Slices: N
  Consumed bullets: M (of K in inbox; remainder stay in inbox for next sprint)

Next: run /compose-sprint in this chat to write the package and archive
the consumed bullets.
```

## Conventions

- **Feedback sprints are still sprints.** They go through the same orchestrator, same buttons, same state machines. They just have `kind = "feedback"` and a `parent_sprint` pointer in the manifest.
- **Parent sprints are immutable.** Never edit `.naml/sprints/<parent-id>/` from a feedback sprint. Always create a child.
- **Tight scope.** Resist bundling unrelated feedback. One feedback sprint = one coherent area of concern.
- **Glossary continuity.** Use the same domain vocabulary as the parent.

## Failure modes to surface

- **Empty inbox** — ask the user to paste feedback inline or run `/note` first
- **Feedback against a sprint that hasn't merged** — ask whether the parent should ship first, or whether this is actually amending an in-flight sprint (it shouldn't be — feedback is post-ship by definition)
- **Feedback that's actually a new feature** — if a bullet describes something out of scope for the parent, recommend running `/grill-me` as a greenfield sprint instead
- **All bullets are trivial typo-level fixes** — surface: "this looks like 5 trivial fixes, totaling ~5 lines of code. Consider just opening a single small PR by hand?" — let the user proceed if they want full pipeline anyway
