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


class RunnerResult(NamedTuple):
    completed: bool   # process exited on its own before the cap
    timed_out: bool   # killed because it hit the wall-clock cap
    exit_code: int    # subprocess exit code (-1 if killed)


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


def _write_current(run_id: str, issue: dict, session_id: str, cap_minutes: int, mode: str) -> None:
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

    if resume:
        cmd = [cfg.claude_bin, "--resume", session_id, "-p", prompt,
               "--dangerously-skip-permissions"]
    else:
        cmd = [cfg.claude_bin, "-p", prompt,
               "--session-id", session_id,
               "--dangerously-skip-permissions"]
        if name:
            cmd[1:1] = ["--name", name]

    _write_current(run_id, issue, session_id, cap_minutes, mode)
    deadline = time.time() + cap_minutes * 60
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
                return RunnerResult(completed=False, timed_out=True, exit_code=-1)

            return RunnerResult(
                completed=True,
                timed_out=False,
                exit_code=proc.returncode,
            )
    finally:
        _clear_current()


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
