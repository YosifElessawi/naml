"""Spawn and resume Claude sessions, enforcing the time cap.

Every issue gets one claude session with a known UUID — assigned by the
orchestrator, passed via ``--session-id``. That UUID is the handle for
everything afterwards: the auto-retry loop resumes it, and you can open it
yourself with ``claude --resume <uuid>`` to peek or take over.

``runner.py`` owns ``current.json`` — written when an agent starts, removed on
exit — so ``orchestrator.py status`` can show what is running.

Multi-account note: this module does NOT force ``CLAUDE_CONFIG_DIR``. The
parent process's env is passed through to the subprocess, so the user switches
between Claude accounts by exporting (or not) ``CLAUDE_CONFIG_DIR`` in the
shell that runs the orchestrator. Default account = ``~/.claude``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import config


class Usage(NamedTuple):
    """Token + cost totals parsed from a Claude `-p` run."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost_usd: float = 0.0
    num_turns: int = 0

    @classmethod
    def empty(cls) -> "Usage":
        return cls()

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "num_turns": self.num_turns,
        }


class RunnerResult(NamedTuple):
    completed: bool   # process exited on its own before the cap
    timed_out: bool   # killed because it hit the wall-clock cap
    exit_code: int    # subprocess exit code (-1 if killed)
    usage: Usage = Usage()  # tokens + cost; zeros when the result wasn't parseable


def compose_prompt(issue: dict) -> str:
    """Build the first-run prompt from an issue's title and body."""
    cfg = config.load()
    title = issue.get("title", "").strip()
    body = (issue.get("body") or "").strip() or "(no description provided)"
    number = issue.get("number")
    return f"""You are working autonomously on GitHub issue #{number} in \
{cfg.repo}. You are already on a fresh feature branch created from \
{cfg.base_branch}.

ISSUE TITLE: {title}

ISSUE BODY:
{body}

INSTRUCTIONS:
- Implement exactly what the issue asks — no more. Make the minimal change.
- Respect the repo's CLAUDE.md and .claude/rules (conventional commits, no
  secrets, atomic commits, validate inputs).
- When done, commit your work with a clear conventional-commit message, then
  end your final message with a one-line summary starting with "DONE:".
- If you genuinely cannot proceed without a human decision, do NOT guess:
  stop, and end your final message with "BLOCKED:" followed by your question.
- Do NOT push, do NOT open a PR, do NOT touch git remotes — the orchestrator
  handles push/PR/merge.
- Do NOT switch branches.

Begin now."""


def compose_retry_prompt(failed_gate: str, tail: str) -> str:
    """Build the prompt that resumes an agent after a validation gate failed."""
    return f"""The validation gate "{failed_gate}" failed on your change. Here \
is the tail of its output:

{tail}

Fix the underlying cause, then commit the fix (conventional-commit message).
Do not push and do not open a PR. End with "DONE:" when the fix is committed,
or "BLOCKED:" with a question if you cannot resolve it."""


def compose_batch_continuation_prompt(issue: dict, index: int, total: int,
                                       prior_issues: list[dict]) -> str:
    """Prompt for issue N (1-indexed) of a batch.

    The session has prior issues in its context. We re-orient the agent: a
    fresh branch has been cut from base, the prior commits are GONE from this
    branch, treat this as a new task informed by the prior work's discussion.
    """
    cfg = config.load()
    title = issue.get("title", "").strip()
    body = (issue.get("body") or "").strip() or "(no description provided)"
    number = issue.get("number")
    prior_lines = "\n".join(
        f"  - #{p.get('number')}: {p.get('title', '').strip()}"
        for p in prior_issues
    ) or "  (none)"
    return f"""You are continuing in the same session, but now on a DIFFERENT \
issue from the same batch in {cfg.repo}.

Batch progress: issue {index}/{total}.

Issues already handled in this session (their commits are on other branches \
— NOT on this one):
{prior_lines}

The orchestrator has already cut a fresh feature branch from {cfg.base_branch} \
for the new issue below. None of the prior work is on this branch; you are \
starting from a clean base.

NEW ISSUE #{number}: {title}

ISSUE BODY:
{body}

INSTRUCTIONS:
- Implement exactly what THIS issue asks — no more. Make the minimal change.
- Treat the prior issues only as context (warm cache, shared understanding) \
  — do NOT re-apply their changes here.
- Respect the repo's CLAUDE.md and .claude/rules.
- When done, commit your work with a conventional-commit message, then end \
  your final message with "DONE:".
- If you cannot proceed without a human decision, stop and end with "BLOCKED:" \
  followed by your question.
- Do NOT push, do NOT open a PR, do NOT touch git remotes — the orchestrator \
  handles those.
- Do NOT switch branches.

Begin now."""


def compose_review_prompt(issue: dict, pr_url: str, diff: str) -> str:
    """Prompt for the auto-review subagent.

    Runs in a FRESH session (not the implementing agent's). Asks for a concise
    plain-English review against the issue's acceptance criteria.
    """
    title = issue.get("title", "").strip()
    body = (issue.get("body") or "").strip() or "(no description provided)"
    number = issue.get("number")
    return f"""You are reviewing a pull request created by another agent.

ISSUE #{number}: {title}

ISSUE BODY:
{body}

PR: {pr_url}

DIFF:
{diff}

INSTRUCTIONS:
- Review against the issue's acceptance criteria FIRST. Did this PR do what \
  the issue asked?
- Flag: missed acceptance criteria, obvious bugs, security issues, \
  out-of-scope changes, missing tests for new behaviour.
- Skip: nitpicks, style preferences, hypothetical future concerns.
- Keep it concise — plain English, no line-by-line code walkthrough unless \
  something is genuinely wrong.
- End your response with one of these verdicts on its own line:
    VERDICT: LGTM
    VERDICT: REQUEST_CHANGES
- Output ONLY the review text. Do not touch files, do not run commands, do \
  not commit."""


def _write_current(run_id: str, issue: dict, session_id: str, cap_minutes: int, mode: str,
                   *, batch_id: str | None = None) -> None:
    cfg = config.load()
    cfg.ensure_log_dir()
    payload = {
        "run_id": run_id,
        "issue_number": issue.get("number"),
        "issue_title": issue.get("title"),
        "session_id": session_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cap_minutes": cap_minutes,
        "mode": mode,
        "dry_run": cfg.dry_run,
        "batch_id": batch_id,
    }
    cfg.current_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _clear_current() -> None:
    config.load().current_json.unlink(missing_ok=True)


def run_agent(
    prompt: str,
    session_id: str,
    run_id: str,
    log_path: Path,
    issue: dict,
    mode: str = "slow",
    *,
    name: str | None = None,
    resume: bool = False,
    batch_id: str | None = None,
) -> RunnerResult:
    """Run (or resume) a Claude session headless, under the time cap.

    On the first run, ``--session-id`` assigns the UUID and ``--name`` sets the
    display name. On a retry, ``resume=True`` re-enters that same session via
    ``--resume``. Output is appended to ``log_path``; on timeout the whole
    process group is killed.

    Inherits ``CLAUDE_CONFIG_DIR`` from the parent env — set it (or don't) in
    your shell to pick a Claude account.
    """
    cfg = config.load()
    cap_minutes = cfg.run_cap_minutes

    env = os.environ.copy()
    # Deliberately do NOT override CLAUDE_CONFIG_DIR — inherit from parent.

    base_flags = ["--output-format", "stream-json", "--verbose",
                  "--dangerously-skip-permissions"]
    if resume:
        cmd = [cfg.claude_bin, "--resume", session_id, "-p", prompt, *base_flags]
    else:
        cmd = [cfg.claude_bin, "-p", prompt, "--session-id", session_id, *base_flags]
        if name:
            cmd[1:1] = ["--name", name]

    _write_current(run_id, issue, session_id, cap_minutes, mode, batch_id=batch_id)
    deadline = time.time() + cap_minutes * 60
    start_offset = log_path.stat().st_size if log_path.exists() else 0
    try:
        with log_path.open("a", encoding="utf-8") as log:
            label = "RESUME" if resume else "START"
            log.write(f"\n{'=' * 60}\nCLAUDE {label} — session {session_id} "
                      f"(cap {cap_minutes}m)\n{'=' * 60}\n")
            log.flush()
            # start_new_session=True puts the agent in its own process group so
            # a timeout kill takes down any child processes it spawned too.
            proc = subprocess.Popen(
                cmd,
                cwd=str(cfg.repo_root),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=max(1, int(deadline - time.time())))
            except subprocess.TimeoutExpired:
                log.write(f"\n[orchestrator] run cap of {cap_minutes}m hit — killing agent\n")
                log.flush()
                _kill_group(proc)
                return RunnerResult(completed=False, timed_out=True,
                                    exit_code=-1, usage=Usage.empty())

            usage = _parse_usage(log_path, start_offset)
            return RunnerResult(
                completed=True,
                timed_out=False,
                exit_code=proc.returncode,
                usage=usage,
            )
    finally:
        _clear_current()


def _parse_final_result(log_path: Path, start_offset: int) -> dict | None:
    """Return the final ``type=result`` event in the log slice, or None.

    The log is shared across multiple invocations (init, retries, review);
    ``start_offset`` is captured before each invocation so we only look at
    events the current run produced.
    """
    try:
        with log_path.open("rb") as fh:
            fh.seek(start_offset)
            last_result: dict | None = None
            for raw in fh:
                line = raw.strip()
                if not line or not line.startswith(b"{"):
                    continue
                try:
                    ev = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(ev, dict) and ev.get("type") == "result":
                    last_result = ev
            return last_result
    except OSError:
        return None


def _usage_from_result(ev: dict | None) -> Usage:
    if not ev:
        return Usage.empty()
    u = ev.get("usage") or {}
    return Usage(
        input_tokens=int(u.get("input_tokens", 0) or 0),
        output_tokens=int(u.get("output_tokens", 0) or 0),
        cache_creation_input_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
        total_cost_usd=float(ev.get("total_cost_usd", 0.0) or 0.0),
        num_turns=int(ev.get("num_turns", 0) or 0),
    )


def _parse_usage(log_path: Path, start_offset: int) -> Usage:
    """Convenience: scan the log slice and return just the usage."""
    return _usage_from_result(_parse_final_result(log_path, start_offset))


def run_oneshot(prompt: str, log_path: Path, *, label: str = "ONESHOT") -> tuple[RunnerResult, str]:
    """Run a fresh, no-session Claude invocation and return its final text.

    Used by the auto-review stage. Inherits ``CLAUDE_CONFIG_DIR`` from the
    parent env just like ``run_agent``. The final ``result`` event's
    ``result`` field is returned as the agent's text output.
    """
    cfg = config.load()
    cap_minutes = cfg.run_cap_minutes
    env = os.environ.copy()

    cmd = [
        cfg.claude_bin, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--dangerously-skip-permissions",
    ]

    deadline = time.time() + cap_minutes * 60
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start_offset = log_path.stat().st_size if log_path.exists() else 0
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n{'=' * 60}\nCLAUDE {label} (no session) (cap {cap_minutes}m)\n{'=' * 60}\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cfg.repo_root),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=max(1, int(deadline - time.time())))
        except subprocess.TimeoutExpired:
            log.write(f"\n[orchestrator] {label} cap of {cap_minutes}m hit — killing\n")
            log.flush()
            _kill_group(proc)
            return RunnerResult(False, True, -1, Usage.empty()), ""

    final = _parse_final_result(log_path, start_offset)
    usage = _usage_from_result(final)
    text = ""
    if final:
        raw_result = final.get("result")
        if isinstance(raw_result, str):
            text = raw_result
    return RunnerResult(True, False, proc.returncode, usage), text


def _kill_group(proc: subprocess.Popen) -> None:
    """Terminate the agent's process group, escalating to SIGKILL."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            continue
