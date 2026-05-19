"""Prompt builders for the implementer and reviewer agents.

Single source of truth for the wording — kept out of the lane worker so the
prompts are easy to inspect and tweak without touching control flow.
"""

from __future__ import annotations

from pathlib import Path

from .package import Slice, Sprint


SUMMARY_FILENAME_TEMPLATE = "state/{slice_id}.summary.md"


def _format_upstream_summaries(summaries: dict[str, str]) -> str:
    """Render the upstream summaries block. Empty if none."""
    if not summaries:
        return ""
    chunks = []
    for slice_id, body in summaries.items():
        chunks.append(
            f"### Upstream summary — {slice_id}\n\n{body.strip()}\n"
        )
    return "## Upstream context (auto-injected)\n\n" + "\n".join(chunks) + "\n\n"


def _format_acceptance_criteria(slice_body: str) -> str:
    """Pluck the AC section out of a slice body for emphasis in the prompt."""
    lines = slice_body.splitlines()
    out: list[str] = []
    in_ac = False
    for line in lines:
        if line.strip().lower().startswith("## acceptance criteria"):
            in_ac = True
            out.append(line)
            continue
        if in_ac and line.startswith("## "):
            break
        if in_ac:
            out.append(line)
    return "\n".join(out).strip()


def implementer_prompt(
    *,
    sprint: Sprint,
    slice_: Slice,
    upstream_summaries: dict[str, str],
    branch_name: str,
    summary_path_relative: str,
) -> str:
    """First-run prompt for the implementer agent.

    Includes: sprint overview, upstream summaries from completed deps,
    the slice body, the acceptance criteria, and the contract that the
    agent must write a per-slice summary file before exiting.
    """
    upstream = _format_upstream_summaries(upstream_summaries)
    ac = _format_acceptance_criteria(slice_.prompt_body) or "(see slice body)"

    return f"""You are working autonomously on a single slice of sprint \
``{sprint.id}`` in {sprint.target_repo}.

You are already on a fresh feature branch ``{branch_name}`` cut from \
``{sprint.base_branch}``. Do NOT switch branches. Do NOT push and do NOT \
open a PR — the orchestrator handles those.

## Sprint overview

{sprint.overview.strip()}

{upstream}## Slice {slice_.id}: {slice_.title}

{slice_.prompt_body.strip()}

## Acceptance criteria (must verify)

{ac}

## Working contract

- Implement EXACTLY what this slice asks — no more. Minimal change.
- Respect the repo's CLAUDE.md and any ``.claude/rules/*`` files (conventional
  commits, no secrets, atomic commits, validate inputs).
- The orchestrator runs the validation gates (lint + tests) AFTER you exit.
  Do NOT run them yourself. Skip the local verification step — it wastes
  time hunting for binaries inside an isolated worktree. If you really
  want to spot-check, the project's ``.venv`` (and similar paths) are
  symlinked into your worktree from the source repo, so commands like
  ``.venv/bin/ruff`` and ``.venv/bin/pytest`` work directly — but you
  do not need to do this.
- Do NOT ``git merge``, ``git cherry-pick``, or otherwise pull upstream
  dependency branches into your worktree. The work from your declared
  ``depends_on`` slices reaches you ONLY as auto-injected upstream
  summaries above. The orchestrator handles dependency composition at
  merge time.
- Do NOT push and do NOT open a PR. The orchestrator handles push, PR
  open, review, and merge.
- Commit your work using a clear conventional-commit message before exiting.
- Before exiting, WRITE a brief summary of what you built to
  ``{summary_path_relative}`` — bullet points, ~10 lines, covering: what
  changed, where it lives, any non-obvious decisions, and anything a
  downstream slice should know. This file is auto-injected into the prompts
  of slices that depend on this one.
- End your final message with one of:
    DONE:    <one-line summary>
    BLOCKED: <one-line question for the human>

Begin now."""


def retry_prompt(*, failed_gate: str, tail: str) -> str:
    """Resume prompt sent to the implementer after a gate failed."""
    return f"""The validation gate "{failed_gate}" failed on your change. \
Here is the tail of its output:

{tail}

Fix the underlying cause, then amend or add a commit (conventional-commit \
message). Do NOT push and do NOT open a PR. End with "DONE:" once the fix \
is committed, or "BLOCKED:" with a question if you cannot resolve it."""


def request_changes_prompt(*, review_text: str, pr_url: str) -> str:
    """Resume prompt sent to the implementer after the reviewer requested changes.

    The reviewer is a fresh, second-opinion agent. The implementer agent
    keeps its session context, so this prompt only needs to hand over the
    review and clarify the contract.
    """
    return f"""The auto-reviewer (a fresh second agent) reviewed your PR \
({pr_url}) and requested changes. Their review:

{review_text}

Address the requested changes. Commit your fix with a conventional-commit \
message. Do NOT push and do NOT touch the PR yourself — the orchestrator \
re-pushes the branch and re-triggers the reviewer once you are done.

End your final message with "DONE:" once the fix is committed, or \
"BLOCKED:" with a question if you cannot proceed."""


def reviewer_prompt(
    *,
    sprint: Sprint,
    slice_: Slice,
    pr_url: str,
    diff: str,
    upstream_summaries: dict[str, str],
) -> str:
    """Prompt for the fresh reviewer agent.

    The reviewer has no implementer-side context — give it everything it
    needs in this single prompt. Output ends with a VERDICT line that the
    orchestrator parses.
    """
    ac = _format_acceptance_criteria(slice_.prompt_body) or "(see slice body)"
    upstream = _format_upstream_summaries(upstream_summaries)

    return f"""You are reviewing a pull request opened by another agent for \
slice ``{slice_.id}`` of sprint ``{sprint.id}`` in {sprint.target_repo}.

## Sprint overview

{sprint.overview.strip()}

{upstream}## Slice {slice_.id}: {slice_.title}

{slice_.prompt_body.strip()}

## Acceptance criteria

{ac}

## Pull request

URL: {pr_url}

### Diff

{diff}

## Review instructions

- Review against the acceptance criteria FIRST. Did the PR do what the slice \
  asked?
- Flag: missed acceptance criteria, obvious bugs, security issues, \
  out-of-scope changes, missing tests for new behaviour.
- Skip nitpicks, style preferences, hypothetical future concerns.
- Keep it concise — plain English, no line-by-line walkthrough unless \
  something is genuinely wrong.

End your response with one of these verdicts ON ITS OWN LINE:

    VERDICT: LGTM
    VERDICT: REQUEST_CHANGES
    VERDICT: ABANDON

Use ABANDON only if the change is unsalvageable (wrong approach, broken \
architecture, has to be redone from scratch). Output ONLY the review text. \
Do not commit. Do not modify files."""
