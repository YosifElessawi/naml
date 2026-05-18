---
name: grill-me
description: Multi-phase conversational design session that turns an idea into a sprint package. Routes through intent → architecture → design → slicing → acceptance criteria. Reads docs/CONTEXT.md, docs/GLOSSARY.md, docs/adrs/ for project context. Streams slice files to disk as each converges. Run before /compose-sprint.
disable-model-invocation: true
---

# Grill Me

A focused, phase-routed grilling session that produces a sprint draft. Adapts depth to sprint size automatically.

## Interaction style (read first)

**Do NOT use the `AskUserQuestion` tool.** It forces canned options and breaks the conversational grilling flow.

Every question is asked as plain text in chat, and each one follows this shape:

1. **The question itself** — open-ended, in the user's own vocabulary.
2. **Your recommended answer** — a concrete default based on what you already know (project context, prior phase answers, codebase signals). Not a list of options to pick from.
3. **Why** — one or two sentences of reasoning behind the recommendation: tradeoffs, ADRs in play, what the codebase suggests, risk you're weighing.
4. **Invite override** — make clear the user can accept, tweak, or replace it.

Example:

> **Where in the codebase does this land?**
> Recommendation: extend `apps/api/routes/policy.py` and add a new `PolicyValidator` service under `apps/api/services/`.
> Why: the existing route already handles auth + request shaping, and ADR-007 says new domain logic goes in `services/`, not inline in routes. Adding the validator as its own service keeps the route thin and makes the validator unit-testable.
> Accept, tweak, or point me somewhere else?

If the user has clearly already decided and stated their preference, skip the recommendation and just confirm — don't manufacture fake alternatives. Recommendations are for the moments where the user would otherwise have to think from scratch.

Only ask one question (or one tight cluster) per turn. Wait for the answer before moving on.

## Process

### Phase 0 — Sprint shape

Open with: "What are we building today?" After the user describes the idea (one or two sentences is fine), classify the sprint shape:

- **Backend / API / infra only** → skip the design phase
- **UI / visual / interaction work** → run the design phase
- **Both** → run architecture first, then design

Also classify expected size from the user's description:

- **Micro** (likely 1-3 slices) → abbreviate phases, ask only the essential questions
- **Typical** (3-8 slices) → full phase flow, normal depth
- **Large** (8+ slices) → full flow, plus push back at the end if it really looks like 15+ slices

Tell the user which mode you've picked and why, in one sentence. They can override.

### Phase 1 — Intent capture

**Load project context — `docs/GLOSSARY.md` is always the entry point:**

1. Read `docs/GLOSSARY.md` first if present. In a multi-context project it describes the context layout and routes to per-app `CONTEXT.md`. In a single-context project it's a flat term list.
2. **Single-context** (root `docs/CONTEXT.md` exists OR no apps/ structure): read `docs/CONTEXT.md` directly.
3. **Multi-context** (`CONTEXT-MAP.md` exists OR `apps/*/CONTEXT.md` exists): don't read every per-app CONTEXT.md upfront. Ask the user which apps/contexts the sprint will touch, then load only those CONTEXT.md files. Cheaper, focused.
4. If neither GLOSSARY.md nor any CONTEXT.md exists: no project context preload; tell the user and proceed.

Then ask:

- **Goal** — what does success look like? One paragraph.
- **Why now** — what's driving this sprint? Demo, customer ask, tech debt, internal need?
- **Non-obvious constraints** — anything the codebase wouldn't tell an agent? (deps frozen, performance budget, security model, etc.)
- **Out of scope** — what's being deferred to a later sprint?

Save the intent as the draft `overview.md` content in conversation context.

### Phase 2 — Architecture (skip for micro-UI sprints)

**Load glossary and ADRs — adapt to layout:**

- Single-context: read `docs/GLOSSARY.md` (if present) + system-wide ADRs in `docs/adrs/` or `docs/adr/`
- Multi-context: read `docs/GLOSSARY.md` (cross-cutting terms, if present) + system-wide ADRs in `docs/adr/` + per-app ADRs in `apps/<app>/docs/adr/` for each app the sprint will touch. Also re-confirm per-app `CONTEXT.md` content if not already in context.

Grill on:

- **Where in the codebase does this land?** Major files / modules.
- **Data flow** — what produces inputs, what consumes outputs?
- **Existing decisions to respect** — surface relevant ADRs by name.
- **Tradeoffs the user is making** — name 2-3 explicitly.
- **New architectural decisions** — anything that should become a new ADR? If yes, draft it (don't write the file yet — that happens in compose-sprint).

This phase is conversational and uses the recommendation-with-reasoning format described in **Interaction style**. Push back when the user is hand-waving over a real decision — don't let a vague answer pass just because a recommendation was offered.

### Phase 3 — Design (only if UI/visual work)

If the sprint involves UI, grill one question at a time, with each question grounded in a concrete visual reference (existing component, mockup, sketch). Push back when the user is hand-waving over visual decisions.

- **Surfaces affected** — which screens / components / flows? Use the project's component library names where applicable.
- **Visual references** — ask the user to paste mockups, point to existing components, or sketch in text. Refuse to proceed on pure verbal descriptions for non-trivial layout decisions.
- **Interaction patterns** — what user actions exist, what state changes do they trigger? Walk through happy-path + edge-path step by step.
- **Edge cases** — empty state, loading, error, no-data, very-long-data, mobile vs desktop if applicable.
- **Accessibility constraints** — keyboard navigation, screen reader behaviour, contrast — surface explicitly if relevant.

Convergence rule: do not exit this phase until every screen mentioned has at least one concrete visual anchor (mockup, sketch, component name) the agent will be able to reference.

### Phase 4 — Vertical slicing

Break the work into **tracer-bullet vertical slices**. Each slice cuts end-to-end through every relevant layer (schema → API → UI → tests), is demoable on its own, and has tight scope.

For each slice capture:

- **Title** — short, uses the project's glossary terms
- **Type** — `AFK` (agent can finish) or `HITL` (needs human in the loop)
- **`depends_on`** — slice IDs that must complete first; empty list means parallel-from-t=0
- **`touches`** — path globs the slice will write to (best-effort guess, exact globs not required)

Quiz the user on:

- Granularity (too coarse? too fine?)
- Dependencies (are these really blocking, or coincidental?)
- AFK/HITL classification (prefer AFK where possible)

**Stream to disk**: as each slice converges, write `slices/slice-N.md` to a temp directory in conversation context (the user is told the temp location, but the final placement happens in `/compose-sprint`). This protects against context compaction in long sessions.

### Phase 5 — Acceptance criteria

For each slice, get 2-4 acceptance criteria. Each is a check the agent can verify:

- "The endpoint accepts X and returns Y"
- "The snapshot test in tests/render/snapshots/ passes"
- "The new component renders in the storybook"

Push back on vague criteria ("works well") — they have to be testable.

### Phase 6 — Artifacts

Ask if the sprint has accompanying artifacts:

- Mock HTML files
- Eval datasets
- Sample inputs/outputs
- Design references

Capture the file paths or content. These will be copied into `artifacts/` by compose-sprint.

### Phase 7 — Wrap

Summarise the draft:

```
Sprint draft ready:
  Title: <title>
  Slices: N  (M AFK, K HITL)
  Dependencies: <topology>
  Artifacts: <list>

Next: run /compose-sprint in this chat to write the package.
```

If the slice count exceeds 15, surface:

> This is shaping up to be N slices. Consider splitting at <natural boundary>?
> Or proceed as-is and let me know in /compose-sprint.

Never block — always let the user override.

## Adaptations for micro-sprints

For 1-3 slice sprints:

- Intent + slicing + AC can be done in a single combined pass
- Architecture phase is collapsed into a single question ("anything tricky here?")
- Design phase only runs if the work is visually heavy enough to warrant it

The goal is to keep the system useful at small scale, not force ceremony.

## Feedback-sprint mode

If invoked via `/grill-feedback`, this skill receives:

- A target parent sprint
- Pre-loaded curated context (parent overview, slice summaries, reviews, ADRs)
- The feedback bullets from `docs/feedback/inbox.md`

In that mode, skip Phase 1's intent capture (the inbox provides it), skip Phase 2's architecture phase (parent's decisions hold), and focus on Phase 4 slicing + Phase 5 acceptance. Output is typically 1-3 slices.

## Conventions to respect

- **Vertical slices, not horizontal layers.** A slice does schema + API + UI + tests for one feature, not "all schemas" for many features.
- **Prefer AFK over HITL.** HITL is only for slices that genuinely need a human decision (a design review, an architectural call).
- **Touch glob honesty.** If a slice might touch a file, declare it. Better to over-declare and serialize than have a hidden conflict at PR time.
- **Glossary vocabulary in titles.** "Add SchemaValidator integration" beats "Add validation thing."
