# Naml — Live Architecture (as built, 2026-05-21)

> **Status:** describes the code that is actually on `main` today, not what
> [DESIGN-V2.md](DESIGN-V2.md) proposed. V2 captured the *intent* at the
> start of the sprint; many decisions changed mid-build (resume semantics,
> session UUID hygiene, signal handling, reconciliation, the cockpit's SSE
> contract). This doc supersedes V2 for "how does naml actually work right
> now"; V2 stays as the historical design baseline.

This is the operator/maintainer doc. If you're trying to understand why
your sprint did what it did, why your tokens disappeared, or how to debug
a stuck slice, start here.

---

## 0. One-paragraph summary

`naml run <sprint-dir>` walks every slice in a sprint package through a
state machine: `pending → setup → work → pr → review → review_passed
→ merged`. Slices are processed in topological order by N worker
threads ("lanes"), one slice per lane at a time. Each lane spawns its
own Claude Code subprocess inside an isolated git worktree. Failures
are recoverable: signals drain or hard-kill lanes; orphans get auto-
reset on the next run; externally-merged PRs get reconciled to
`merged`. The cockpit (`naml serve`) is a separate aiohttp process
that streams state to a browser via SSE.

---

## 1. Process model

```
┌───────────────────────── naml run (one process) ─────────────────────────┐
│ main thread:                                                              │
│   load_config → load_sprint → preflight_gates → run_sprint()              │
│      └─ install SIGINT/SIGTERM handlers                                   │
│      └─ Scheduler.absorb_existing_statuses (orphan auto-recovery)         │
│      └─ reconciler.reconcile_sprint (external-merge promotion)            │
│      └─ spawn N lane threads (daemon=False)                               │
│      └─ join all lane threads, write final sprint state                   │
│                                                                           │
│ lane thread 1 .. N (parallel):                                            │
│   while not shutdown:                                                     │
│     slice_id = scheduler.pop_ready()                                      │
│     lane.process_slice(slice_id, ctx)   # the per-slice state machine     │
│        └─ subprocess.Popen("claude ...", start_new_session=True)          │
│              ├─ tee thread (line-buffered stdout → log + TurnEmitter)     │
│              └─ Claude Code subprocess (its own process group)            │
└───────────────────────────────────────────────────────────────────────────┘

┌──────────────── naml serve (a different process, optional) ──────────────┐
│  aiohttp app:                                                             │
│    /events            (SSE: snapshot, state-update, metric-tick, ping)    │
│    /api/state         (JSON, ETag)                                        │
│    /config            (JSON, settings shape)                              │
│    /aggregates        (snapshot of in-memory aggregator)                  │
│    /aggregates/reset  (drop + replay)                                     │
│    /intervene/<id>    (POST: hold/resume/abandon)                         │
│  Watches `.naml/sprints/<id>/state/` JSONL files; never spawns Claude.    │
└───────────────────────────────────────────────────────────────────────────┘
```

**Key invariant:** `naml run` and `naml serve` are independent processes
that share state only via files under `.naml/sprints/<id>/state/`. There
is no IPC. The cockpit observes; the runner mutates.

---

## 2. State machines (the canonical contract)

### 2.1 Slice state machine (per-slice `status.json`)

```
                            ┌─────────────────────────┐
                            │  pending  (fresh slate) │
                            └────────────┬────────────┘
                                         │ lane.pop_ready()
                                         ▼
                            ┌─────────────────────────┐
                            │  setup                  │── worktree_add fails ──► failed
                            └────────────┬────────────┘
                                         │
                                         ▼
                            ┌─────────────────────────┐
              ┌────HOLD ◄───┤  work                   │
              │             │  (gate-fix loop:        │── implementer crash ──► failed
              │             │   up to max_retries     │── gate fails N times ─► failed
              │             │   --resume the same     │
              │             │   session_id)           │
              │             └────────────┬────────────┘
              │                          │ diff produced
              │                          ▼
              │             ┌─────────────────────────┐
              ▼             │  pr                     │── gh push/create fails ─► failed
       ┌────────────┐       └────────────┬────────────┘
       │   held     │                    │ stop_after=review (default)
       │ (sentinel  │                    ▼
       │  on disk;  │       ┌─────────────────────────┐
       │  spin loop │       │  review                 │
       │  waits)    │       │  (FRESH reviewer        │── reviewer cap hit ───► needs_human_review
       └─────┬──────┘       │   session, up to 3      │── ABANDON verdict ────► needs_human_review
             │ clear        │   attempts)             │
             │ sentinel     └──────┬──────────────┬───┘
             ▼                     │ LGTM         │ REQUEST_CHANGES
       (back to work,              │              │ → resume implementer (--resume)
        --resume session)          │              │ → re-run gates → re-push → re-review
                                   │              │
                                   ▼              │
                       ┌─────────────────────────┐
                       │  review_passed          │
                       │  (lane is done; sprint  │
                       │   waits for naml merge) │
                       └────────────┬────────────┘
                                    │ naml merge <sprint>
                                    ▼
                            ┌────────────────────┐
                            │  merging           │── any tier fails ──► merge_blocked
                            └─────────┬──────────┘     (Tier 4 escalation)
                                      │
                                      ▼
                            ┌────────────────────┐
                            │  merged (terminal) │
                            └────────────────────┘
```

Terminal-failure states the user must clear with `naml retry`:
`failed`, `needs_human_review`, `blocked_upstream`, `merge_blocked`,
`needs_info`, `abandoned`.

### 2.2 Sprint state machine (per-sprint `sprint.json`)

```
package_received → planning → executing → awaiting_signoff
                                  │             │
                                  │             └─► merging → complete
                                  │                   │
                                  │                   └─► merge_blocked
                                  │
                                  ├─► partial_failure   (some slices failed, others done)
                                  ├─► failed            (gate preflight failed; nothing ran)
                                  └─► (state stays "executing" but transitions
                                       record "run interrupted (drain pause)" when
                                       the user Ctrl-C's — see §6)
```

### 2.3 Project state machine (`.naml/state.json`)

```
idle → active → awaiting_human (last sprint left a terminal-failure state)
            ↘ active           (next naml run picks up)
```

---

## 3. The lane (per-slice loop) — `naml/lane.py`

`process_slice()` is the ~450-line function that drives one slice from
`pending` to a terminal state. Reading it top-to-bottom:

### 3.1 Held-resume detection (first check)

If the slice's on-disk state is `held` AND its `worktree` + `session_id`
+ `branch` are all set AND the worktree directory still exists, the
lane treats this as a **cross-restart HELD resume**. It skips setup,
keeps the same session UUID, waits for the user to clear the
`held.sentinel` file via `/intervene`, then jumps into the work loop
with `resume=True`.

If anything is missing, fall through to fresh setup.

### 3.2 Fresh setup

1. Transition `pending → setup`.
2. Compute the worktree path: `<lane_root>/<repo_slug>/<sprint_id>/<slice_id>/`.
3. `git worktree add` from the base branch.
4. `git checkout -b naml/<sprint_id>/<slice_id>`.
5. **Generate a fresh `session_id = uuid.uuid4()`.** This is critical:
   re-using a session UUID across `naml run` invocations makes Claude
   reject the spawn with `Session ID is already in use`. Even orphan
   recovery wipes `session_id` (see §5.1).

### 3.3 Work + gate-fix loop

The implementer is invoked with the full prompt the FIRST time, then
`resume=True` on every subsequent turn within this slice attempt:

```
result = run_implementer(prompt=full_prompt, session_id, resume=False)
gate_result = run_gates(cfg.gates, cwd=worktree)

retries = 0
while not gate_result.passed and retries < cfg.max_retries:  # default 2
    retries += 1
    retry_text = prompts.retry_prompt(failed_gate, tail)
    result = run_implementer(prompt=retry_text, session_id, resume=True)
    gate_result = run_gates(...)
```

`cfg.max_retries` defaults to 2 → up to **3 total work attempts**.

### 3.4 PR open

`git push origin <branch>` then `gh pr create`. The PR URL is written
back to `status.pr_url`.

If `stop_after=pr` (rare; used by `naml run --stop-after pr`), the
lane returns here.

### 3.5 Review loop

Up to `states.RETRY_CAPS[REVIEW] = 3` attempts:

```
for attempt in range(1, cap + 1):  # 1, 2, 3
    diff = gh pr diff <branch>
    review_result = run_reviewer(prompt=...)        # FRESH session every time
    verdict = parse_verdict(review_result.final_text)

    if verdict == LGTM:           → review_passed (terminal-success for lane)
    if verdict == ABANDON:        → needs_human_review
    if verdict in {REQUEST_CHANGES, UNKNOWN}:
        if attempt == cap:        → needs_human_review
        run_implementer(prompt=fix_prompt, session_id, resume=True)
        run_gates() loop (same max_retries as §3.3)
        re-push
        # next iteration runs a fresh reviewer on the new diff
```

The reviewer is **always a fresh Claude session** — no `--session-id`,
no `--resume`. The implementer always **resumes its original session**
to apply review changes.

---

## 4. Claude session lifecycle (the part you most want to understand)

### 4.1 Per-slice session count — the math

For ONE slice in ONE `naml run` invocation, walking the happiest path:

| Phase | Claude sessions | Notes |
|---|---|---|
| Setup | 0 | git only |
| Work (no gate fails) | 1 implementer session, 1 turn | fresh UUID |
| PR open | 0 | git + gh only |
| Review (LGTM first try) | 1 reviewer session, 1 turn | fresh, no UUID |
| **Happy path total** | **2 sessions** | |

Worst pre-merge path (max retries everywhere):

| Phase | Sessions | Turns inside that session |
|---|---|---|
| Work + 2 gate retries | 1 implementer | 3 turns (initial + 2 retries via `--resume`) |
| Review attempt 1: REQUEST_CHANGES | 1 fresh reviewer | 1 |
| Implementer applies review | same implementer | +1 turn via `--resume` |
| Implementer fixes broken gate | same implementer | +up to 2 retry turns |
| Review attempt 2: REQUEST_CHANGES | 1 fresh reviewer | 1 |
| Apply review + re-fix gates | same implementer | +1 .. +3 turns |
| Review attempt 3 | 1 fresh reviewer | 1 |
| **Worst pre-merge total** | **4 sessions** (1 impl + 3 reviewer) | impl session has 7-10 turns |

Merge phase (separate `naml merge` run):

- Tier 1 (pure git/gh): **0 Claude sessions**.
- Tier 2 (scripted resolvers): **0 Claude sessions**.
- Tier 3 (merger agent): **1 fresh merger session**, 1 attempt.
- Tier 4: **0 Claude sessions** (human escalation, PR comment posted by `gh`).

### 4.2 Session UUID hygiene (the rule that broke us mid-sprint)

Claude Code rejects a spawn that reuses a consumed session UUID with
`Session ID is already in use`. This caused multiple slice failures
during cockpit-v2 until we hardened it.

The rule, enforced in code:

- **Every lane claim of a slice generates a fresh `session_id`.** See
  `_fresh_session_id()` in `lane.py`. The previous run's UUID is
  discarded.
- **`naml retry`** wipes `session_id` to empty string. The next run
  generates a fresh one.
- **Orphan auto-recovery** (`scheduler._recover_orphan_locked`) wipes
  `session_id` to empty string.
- **Held resume IS the exception:** the same `session_id` IS reused
  with `--resume` because the prior naml run never terminated this
  Claude session (the lane was parked in the held spin loop, not
  killed). The UUID is still valid because Claude considers a session
  consumed only when its result event is emitted.

### 4.3 What `--resume` actually does

`claude --resume <uuid> -p <prompt>` re-enters the session with its
full prior context (file reads, todos, prior tool calls, reasoning).
The new prompt is appended as the next user turn. Token usage
**continues to grow** across resumes within the same session — every
resume reads the entire prior transcript. This is why we saw slice-3
hit context-window walls on its 5th resume.

Within one slice attempt:
- Initial: `run_implementer(prompt=full_implementer_prompt, resume=False)` → spawns with `--session-id <new-uuid>`.
- Gate retry: `resume=True` → `claude --resume <uuid> -p <retry_prompt>`.
- Review-driven fix: `resume=True` again → same UUID, more context.

### 4.4 Process spawn details

Every Claude subprocess (implementer / reviewer / merger) is spawned
with `start_new_session=True`, putting it in its own process group.
This is what lets the operator Ctrl-C `naml run` (which reaches naml
only) without immediately killing the live Claude sessions. We choose
when to forward the signal.

Spawn arguments are constructed in `naml/claude.py`:

```python
[claude_bin,
 "-p", prompt,
 "--session-id", session_id,    # or "--resume", session_id
 "--output-format", "stream-json",
 "--verbose",
 "--permission-mode", "bypassPermissions",
 "--dangerously-skip-permissions",
 "--allowedTools", <whitespace-separated allowlist>]
```

Three different allowlists:

| Agent | What it can do | Cannot do |
|---|---|---|
| Implementer | Read/Write/Edit/MultiEdit, all Bash, git commit / merge / rebase / cherry-pick / revert, web fetch/search | `git push`, `gh pr merge` (naml does these) |
| Reviewer | Read/Glob/Grep + `gh pr diff`, `gh pr view`, `git diff`, `git log`, `git show`, WebFetch/Search | Any write tool, any state-mutating bash |
| Merger | Read/Write/Edit, `git add`, `git rebase --continue`, package manager installs | `git push`, `gh pr merge` (naml finalises) |

### 4.5 Token + cost capture (TurnEmitter)

The implementer's stdout is piped through a `tee` thread that re-writes
each line to the slice log AND feeds it to `TurnEmitter`. The emitter
parses each `type:result` event's `usage` block and appends a row to
`.naml/sprints/<id>/state/slice-<n>.tokens.jsonl`. The aggregator
watches those JSONL files, computes per-slice / per-sprint / per-project
rollups, and broadcasts `metric-tick` SSE events. The reviewer and
merger DON'T emit JSONL (they're one-shot), but their final usage IS
parsed and logged.

---

## 5. Retry surfaces (every layer of "retry" in the system)

Five distinct retry mechanisms operate at different layers. Confusing
them is the #1 way to misread the codebase.

### 5.1 Orphan auto-recovery (scheduler, run-start)

Fires once at the start of every `naml run`. For every slice with a
non-terminal on-disk state (`setup`, `work`, `pr`, `review`, `merging`,
…) that has no live process owning it, the scheduler:

1. Resets state to `pending`.
2. Clears `attempts = {}`.
3. **Clears `session_id`** (Bug 2 — see §4.2).
4. Clears `last_error`.
5. Records a transition: `orphaned in <prev_state> by previous run — auto-recovered`.

The lane then claims the slice fresh, the same way it would after `naml retry`.

### 5.2 External-merge reconciliation (start of run + start of merge)

`naml reconcile <sprint-dir>` (also auto-called at the start of `naml
run` and `naml merge`) walks every non-`merged` slice that's in a
reconcilable state and asks `gh pr list` whether its PR is `MERGED`.
If yes, transitions the slice to `merged` with a detail string carrying
the prior state. Always refreshes the sprint.json rollup from
per-slice files, even if no promotions happened — catches drift.

### 5.3 Gate-fix retry (lane, intra-attempt)

Inside one slice attempt's work phase, if a gate (e.g. `pnpm test`)
fails, the implementer is given the failing gate's stdout tail and
asked to fix it, then gates re-run. Up to `cfg.max_retries` (default
2) retries per gate-fix loop, controlled by `_record_attempt(status,
"work")`. Same session UUID, `--resume`d.

### 5.4 Review retry (lane, review loop)

Up to `RETRY_CAPS[REVIEW] = 3` reviewer attempts per slice. Each
reviewer call is a FRESH Claude session. Between attempts, the
implementer resumes its same session to apply the review changes,
and gate retries (§5.3) can fire again.

### 5.5 `naml retry <slice-id>` (user-driven)

CLI command. Only legal on slices in `LANE_FAILED_STATES` (failed /
needs_human_review / merge_blocked / etc). Resets state to `pending`,
wipes `attempts`, wipes `session_id`, wipes `last_error`. The next
`naml run` picks the slice up via the normal lane mechanic.

---

## 6. Pause + resume (signal handling — `naml/run.py`)

`run_sprint()` installs `SIGINT` and `SIGTERM` handlers on entry,
restores them on exit. The handlers implement a two-stage pause:

### 6.1 First Ctrl-C — drain pause

- `_pause_requested` event is set.
- `Scheduler.shutdown()` is called — `pop_ready()` returns `None`
  for any subsequent claim.
- **In-flight Claude sessions keep running** until they emit their
  result event (or the cap fires). The lane writes their terminal
  status normally.
- After every lane drains, sprint state writes a transition row
  `run interrupted (drain pause)` but **does not** flip sprint state
  to a terminal value (stays `executing`). Project state writes
  `awaiting_human` with `paused` annotation.
- CLI exits 0 with a "resume via `naml run` again" hint.

### 6.2 Second Ctrl-C (or SIGTERM) — hard pause

- `_kill_requested` event is set.
- `claude_mod.terminate_all_active_lanes()` walks the
  `_active_procs` registry and `SIGTERM`s every live Claude
  subprocess (then `SIGKILL` after a 10s grace).
- Lane workers see their subprocess die, return false from
  `_result_ok`, write `status.last_error = "implementer exited
  unexpectedly (code -1)"` and transition to `failed`.
- These `failed` slices need `naml retry` to re-attempt on the next
  run.

### 6.3 Resume mechanics

A subsequent `naml run` on a paused sprint just works:

1. `scheduler.absorb_existing_statuses` runs (§5.1) → orphans get
   auto-recovered to `pending`. Drain-paused slices that were
   mid-flight when SIGINT fired land here.
2. `reconciler.reconcile_sprint` runs (§5.2) → externally-merged
   PRs get promoted.
3. Already-merged / already-review-passed / already-failed slices
   are absorbed at their terminal states (dependents released or
   blocked).
4. Lanes spawn and process the remaining ready slices.

No special "resume" command. `naml run` IS resume.

### 6.4 The HELD state (cockpit-only intervention)

The `held` state is the safe-pause-one-slice path. The user clicks
"Hold" in the cockpit; the server writes a sentinel file
`.naml/sprints/<id>/state/<slice_id>.hold.sentinel`. Each lane checks
for the sentinel at safe-checkpoint boundaries inside the work loop
(between Claude turns, between gate runs). When seen, the lane:

1. Transitions slice to `held`.
2. Enters `_await_hold_clearance` — a spin loop that wakes every 2s
   to re-check the sentinel.
3. Sentinel cleared → transitions back to `work` and `--resume`s the
   same Claude session.

The session UUID survives because the implementer subprocess is still
alive (it returned cleanly after its last turn; the lane just hasn't
spawned the next one).

---

## 7. The cockpit (read-only observer — `naml/server.py`)

`naml serve` is independent of `naml run`. It:

1. Builds an in-memory `Aggregator`.
2. Cold-start replays all `.naml/sprints/<id>/state/*.tokens.jsonl`
   into the aggregator (this is what gives you correct cost totals
   even on a fresh server boot).
3. Starts a `Watcher` thread that tails the JSONL files for new lines.
4. Serves `/events` (SSE), `/api/state`, `/config`, `/aggregates`, etc.

The cockpit `<-> server` contract:

- **SSE event types:** `snapshot`, `state-update`, `metric-tick`, `ping`.
- **Snapshot** is full state; sent on connect.
- **State-update** is per-slice or per-sprint deltas.
- **Metric-tick** is per-event rollups (debounced 100ms in
  `metric_tick.py`).
- **Ping** is keep-alive every 2s — drives the cockpit's sync dot.

The cockpit ALSO has a `/config` endpoint that translates the loaded
`NamlConfig` into the Settings view's `NamlSettings` shape.

---

## 8. State files (the system of record)

```
.naml/
├── state.json                              ← project-level state
└── sprints/
    └── <sprint-id>/
        ├── manifest.toml                   ← static input
        ├── slices/                         ← static input (per-slice prompts)
        ├── artifacts/                      ← grilling outputs (ADRs, mockups)
        └── state/                          ← orchestrator OUTPUT
            ├── sprint.json                 ← sprint-level state + slices rollup
            ├── slice-1.status.json         ← per-slice state machine
            ├── slice-1.summary.md          ← implementer's end-of-run summary
            ├── slice-1.tokens.jsonl        ← per-turn token/cost rows (append-only)
            ├── slice-1.hold.sentinel       ← present iff held
            ├── merge-log.json              ← naml merge audit log
            ├── aggregates-history.jsonl    ← daily-rollover history
            └── feedback-inbox.json         ← cached inbox-mode poll results
```

**Rule:** the orchestrator OWNS these files. The cockpit, this doc,
and the `/intervene` endpoint never edit them by hand. Manual edits
corrupt the state machine. Use `naml retry` / `naml reconcile` /
`/intervene` instead.

---

## 9. Tier-N merger (separate command — `naml merge`)

The merger is a SEPARATE run from the implementer pipeline. After
`naml run` finishes a sprint at `awaiting_signoff`, the user (or the
cockpit) invokes `naml merge`. Per slice in topological order:

| Tier | Approach | When it runs |
|---|---|---|
| 1 | `git rebase origin/<base> → push → gh pr merge` | always tried first |
| 2 | Scripted file resolvers (lockfiles, ADR numbers, etc.) | Tier 1 reported rebase conflict |
| 3 | Fresh `run_merger` Claude session — edits files, `git rebase --continue` | Tier 2 had no resolver for a conflict |
| 4 | Post a PR comment, mark slice `merge_blocked`, sprint becomes `merge_blocked` | Tier 3 still didn't resolve |

Tier 1/2/4 use zero Claude tokens. Tier 3 uses one fresh session.

---

## 10. Token economics (where the bill goes)

Numbers from cockpit-v2 (real measured data, 14 slices):

- Lifetime cost: **$717.61**
- Today (first day of building): $249.69
- Tokens out: ~99,704
- Cache reads: 320,772,587 (massive — implementer resumes hit the cache hard)

Cost per slice averaged **$51.26**. Highest-cost slice was slice-10 at
$48.19 alone (96% ctx window at peak — hit the context wall, needed
recovery).

Where the cost concentrates:

1. **Implementer sessions** account for ~75% of slice cost. The longer
   the resume chain (more gate retries, more review iterations), the
   bigger the transcript that gets reprocessed every turn.
2. **Reviewer sessions** are smaller — fresh session, reads the diff
   + slice spec, emits a verdict. Usually <$1.
3. **Merger sessions** are rare (only fire on Tier 3) — fresh session,
   reads conflicts, edits files. Usually <$2.

The framework tax vs a single naked Claude session:

- A 14-slice sprint sequentially in ONE session might be 50 turns ≈ $150-250.
- The same sprint through naml: 14 implementer sessions × ~5-8 turns each + ~14 reviewers + ~3 mergers = 80-120 turns total **but spread across N parallel lanes** so wall-clock is faster.
- Net: ~3× the token cost for ~2-3× the wall-clock speedup, with the bonus of auto-review catching bad commits and isolated worktrees preventing cross-contamination.

This is the central trade-off naml makes today. Whether it's worth it
depends on the project. For high-stakes shared codebases where review
+ isolation matter, yes. For solo throwaway projects, probably no.

---

## 11. Known gaps + drift from V2 design

What the V2 doc says vs. what's actually built:

| V2 design said | Reality on main |
|---|---|
| "Burst protocol enforced at scheduling" | Not implemented; lanes run unbounded within `parallel_lanes_max`. |
| "Feedback machinery auto-publishes" | Manual via `/grill-feedback`; auto-publish is roadmap item Phase 8 (#10). |
| "Stacked PRs for dependent slices" | Not implemented; slices serialize through topological order. Phase 7 (#9). |
| "ADR auto-write from grilling" | Manual; grill skill emits ADR drafts but doesn't commit them. |
| `model_context_max` | Defaults to 200k; not derived from model name. Causes spurious context warnings on `[1m]` model. |
| Tier-3 merger retry | One-shot; no retry-with-feedback loop. |
| Cockpit web UI | MVP-with-gaps. Center pane translators (`useDashboardLive`/`useStepperLive`/`useLanesLive`) exist but use slim shapes; true Lanes gantt timeline is TBD. |

---

## 12. Glossary of "session" (what the word means in different contexts)

- **Claude session** — one invocation of `claude --session-id <uuid>`
  or `claude --resume <uuid>`. Carries conversation context across turns.
- **Lane "attempt"** — one `lane.process_slice()` call. Spans one or
  more Claude sessions (typically one implementer + one or more
  reviewers).
- **`naml run` invocation** — one execution of the `naml run` CLI.
  Spans many lane attempts and many slices.
- **Anthropic "session" / "weekly" budgets** — billing-side concept,
  unrelated to Claude session UUIDs.

---

## 13. Reading order if you want to verify any of this

1. `naml/states.py` — state-machine constants (single source of truth).
2. `naml/lane.py` — `process_slice` is the heart of it.
3. `naml/claude.py` — `run_implementer` / `run_reviewer` / `run_merger`.
4. `naml/run.py` — top-level orchestration + signal handling.
5. `naml/scheduler.py` — DAG, orphan auto-recovery, ready-queue.
6. `naml/reconciler.py` — external-merge promotion.
7. `naml/merger.py` — Tier 1/2/3/4 flow.
8. `naml/server.py` — cockpit HTTP surface.

If anything in this doc disagrees with the code, the code is right
and this doc is stale — open a PR.
