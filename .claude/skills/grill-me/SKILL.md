---
name: grill-me
description: Multi-phase conversational design session that turns an idea into a sprint package. Routes through intent → architecture → design → slicing → acceptance criteria. Spins up a localhost visual scratchpad (HTML mockups + mermaid diagrams) so every architectural / UI decision is grounded in a visual the user can actually look at. Reads docs/CONTEXT.md, docs/GLOSSARY.md, docs/adrs/ for project context. Streams slice files to disk as each converges. Run before /compose-sprint.
disable-model-invocation: true
---

# Grill Me

A focused, phase-routed grilling session that produces a sprint draft. Adapts depth to sprint size automatically. **Always grounds decisions in visuals served from a local HTTP server** — HTML mockups for UI, mermaid diagrams for architecture, side-by-side option comparisons with a recommended pick badged.

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

## Visual scratchpad (set up once at session start)

Every grilling session produces visual artifacts the user can open in a browser. **This is not optional.** Verbal descriptions don't survive a multi-question session; HTML mockups do, and they double as the design spec the implementer agents will reference.

### Where the scratchpad lives

```
<project-root>/.naml/scratch/grill-<sprint-slug>/mockups/
  cockpit.css         ← shared design tokens (created once, reused per question)
  index.html          ← landing page linking to every question + decision status
  q1-<topic>.html     ← one HTML per question with 2–3 visual options
  q2-<topic>.html
  …
```

`.naml/scratch/` is naml's working area (gitignored). At `/compose-sprint` time, only the mockups files referenced by slices migrate to `.naml/sprints/<id>/artifacts/mockups/`. The scratch dir is then safe to delete.

If the project doesn't have a `.naml/` directory yet (skill is being used outside naml's flow), fall back to `/tmp/grill-<sprint-slug>/mockups/`.

### Start the server (early, before Phase 1)

Run in the background:

```bash
cd <scratchpath> && python3 -m http.server 8765
```

Use `run_in_background: true`. Tell the user the URL once:

> Visual scratchpad live at **http://localhost:8765**. I'll drop one HTML per decision; refresh after each.

If port 8765 is taken, pick the next free port (8766, 8767, …) and tell the user.

### Bootstrap `cockpit.css` (first question only)

If `cockpit.css` doesn't exist in the scratch dir yet, write it with these design tokens (dark cockpit aesthetic — sharp, monospace + sans mix, state-machine color palette):

```css
:root {
  --bg-0: #0a0d12; --bg-1: #0f131a; --bg-2: #151b24; --bg-3: #1c2330;
  --border: #2a3342; --border-soft: #1f2733;
  --text-0: #e7ecf3; --text-1: #b9c2cf; --text-2: #7c8696; --text-3: #4d5666;
  /* state colors */
  --c-work: #38bdf8; --c-pr: #a78bfa; --c-review: #fbbf24;
  --c-merged: #34d399; --c-failed: #f87171; --c-blocked: #94a3b8;
  --c-accent: #38bdf8; --c-accent-2: #34d399;
  --c-warn: #fbbf24; --c-danger: #f87171;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
  --sans: -apple-system, "Inter", system-ui, sans-serif;
}
body { background: var(--bg-0); color: var(--text-0); font-family: var(--sans); }
```

Extend it as needed per question, but keep the token list canonical. For naml's own design work, copy `~/Desktop/Naml/mockups/cockpit.css` as the starter — it's the most complete version of this stylesheet.

### Question-page anatomy

Every `q<N>-<topic>.html` follows the same shape (consistency is what makes the user able to navigate at speed):

1. **Page header** — `Question NN / <surface>` tag + the question in plain English + 1-2 sentence framing.
2. **Recommendation banner** — your concrete recommendation up front, badged in cyan.
3. **2–3 option cards side by side**, each with:
   - **Option tag** (`OPTION A`, etc.) + short title
   - **Mini-mockup or diagram** showing the option visually (HTML/CSS for UI; inline SVG or mermaid for architecture; ASCII-style for terminal/CLI)
   - **Why** paragraph — what this option does well
   - **Wins / Costs** two-column ledger
   - The recommended option gets a `RECOMMENDED` badge + cyan border-glow
4. **Footer with side decisions** — micro-questions the user should weigh in on alongside the main pick.
5. **Nav** — `← all questions` link to `index.html`, `← previous Q` link.

### Mermaid diagrams for architecture / state

For architecture questions, embed mermaid diagrams inside the option mini-mockups. Either inline SVG (if you're comfortable producing it) or via mermaid.js loaded from a CDN — but localhost may not have internet, so inline SVG is the safer default. The mockups in `~/Desktop/Naml/mockups/q4-sprint-state.html` and `q8-sync.html` show how to draw state machines + data flow as inline SVG without any library dependency.

### Index page (`index.html`)

A simple linked list of every question with status:

```
Q1 — Shell & information architecture     A · DONE
Q2 — Header                                A · DONE
Q3 — Dashboard layout                      OPEN
Q4 — Sprint state visualization            QUEUED
…
```

Update it after each question is answered (mark `OPEN → A · DONE`). This gives the user a one-glance progress map and lets them jump back to any question to revisit.

### Iteration: a question can fail

If the user pushes back on all options ("I don't like any of these"), don't keep arguing. Spawn a `q<N>b-<topic>.html` with new hybrid options drawn from their feedback. The Q6 → Q6b and Q7 → Q7b pattern is the canonical example — original question file stays for context, new file supersedes it. Update `index.html` to mark the old as `→ Q<N>b`.

### Final assembly mockup (optional, end of session)

After all decisions are locked, optionally produce `cockpit-final.html` (or `<feature>-final.html`) that stitches every chosen option into one live, interactive page. This is the artifact the implementer slices will reference as ground truth. Worth it for sprints that touch multiple UI surfaces; skip for backend-only sprints.

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

**Use the visual scratchpad for any decision with a graph shape.** Data flow, state machines, message protocols, deployment topologies — produce an `q<N>-<topic>.html` page with the diagram(s) inline (SVG). When comparing approaches (e.g. polling vs SSE vs WebSocket), each option gets its own mini-diagram in the option card. Don't hand-wave architecture in chat when 30 lines of SVG can show it.

This phase is conversational and uses the recommendation-with-reasoning format described in **Interaction style**. Push back when the user is hand-waving over a real decision — don't let a vague answer pass just because a recommendation was offered.

### Phase 3 — Design (only if UI/visual work)

If the sprint involves UI, **every question gets its own HTML mockup page on the localhost scratchpad** with 2–3 visual options side by side. Refuse to proceed on pure verbal descriptions; the visual scratchpad is the contract that converts "I think it should look like…" into a thing you can both look at.

Grill one question at a time. For each surface, ask in this order:

- **Surface identification** — which screen / component / flow are we deciding? Name it.
- **Mockup the options** — produce `q<N>-<surface>.html` with 2–3 layouts/treatments. Recommend one with the badged option. Use real-looking content (proper text, not Lorem Ipsum). Cite where the option idea comes from (existing component? library reference? user's verbal sketch?).
- **Interaction patterns** — once the layout is locked, walk through user actions step by step (happy path + edge path) using the chosen mockup as the surface.
- **Edge cases** — empty state, loading, error, no-data, very-long-data, mobile vs desktop. Each gets its own variant in the mockup if non-trivial.
- **Accessibility constraints** — keyboard navigation, screen reader behaviour, contrast — surface explicitly. If you're unsure whether the recommended layout passes WCAG AA contrast, say so.

**Convergence rule:** do not exit this phase until every screen mentioned has a corresponding mockup file on the scratchpad that the user has signed off on (or explicitly deferred). "Locked, A is good" is the green light. "I dunno, looks fine" is not — push back for a real decision.

**Iterate via Q<N>b**: if the user pushes back on every option, produce a hybrid `q<N>b-<surface>.html` drawing from their feedback. Don't keep arguing the original options.

**At end of Phase 3 (optional but recommended for multi-surface sprints):** produce `cockpit-final.html` (or `<feature>-final.html`) stitching every chosen option into one live interactive page with tab switching + drawer open/close + route navigation. This becomes the ground-truth artifact every slice prompt references.

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

The scratchpad files from Phase 2 + Phase 3 are already candidate artifacts. Confirm with the user which ones survive into the sprint package:

- **Every locked mockup** (`q<N>-<surface>.html` that drove a decision) — yes, always include
- **Superseded mockups** (`q6.html` after `q6b.html` won) — usually skip, but keep if the comparison is useful for implementer context
- **`cockpit-final.html` (or equivalent final assembly)** — yes, always include if produced
- **`cockpit.css`** — yes, include; implementer slices will port it to real CSS-modules / tokens
- **`index.html`** — skip; it's a session navigator, not implementation context

Also ask about non-scratchpad artifacts:

- Eval datasets
- Sample inputs/outputs the implementer will need
- External design references (Figma exports, screenshots from another product)

Capture the file paths or content. `/compose-sprint` copies the locked-in scratchpad files into `.naml/sprints/<id>/artifacts/mockups/` and the non-scratchpad items into `.naml/sprints/<id>/artifacts/`.

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
- **Visuals are mandatory, not optional.** Every architectural-or-design decision gets a mockup page on the localhost scratchpad. Verbal-only decisions don't survive context compaction and produce sprints that drift from intent at implementation time.
- **Lead with a recommendation.** Each question's HTML page shows your recommended option badged. The user accepts, tweaks, or replaces — never picks from a menu of equal alternatives. Recommendations are the value the skill adds.
- **Mockups outlive the chat.** The user can re-open the localhost page tomorrow and pick up where they left off. Treat scratchpad files as durable, not throwaway.
