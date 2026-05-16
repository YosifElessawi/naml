#!/usr/bin/env python3
"""agents-orchestrator — entry point and mode drivers.

Subcommands:
    slow     process at most one issue, then exit (default; launchd calls this)
    burst    loop until the queue / Pro window / time / count cap is hit
    status   print the dashboard and exit
    --help   show usage

Both modes share ``process_one_issue`` — only the outer driver differs.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


# Allow running as a script (``python3 orchestrator.py``) by inserting the
# package's parent dir onto sys.path. When imported as
# ``agents_orchestrator.orchestrator`` the relative imports below are used.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agents_orchestrator import claude_session, config, github_ops, runner, status, validator
else:
    from . import claude_session, config, github_ops, runner, status, validator


# --- helpers --------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "task"


def _run_id(issue_number: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-issue{issue_number}"


def _append_run_record(record: dict) -> None:
    cfg = config.load()
    cfg.ensure_log_dir()
    with cfg.runs_jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _log(msg: str) -> None:
    print(f"[ao {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# --- single-issue inner loop ---------------------------------------------

def process_one_issue(mode: str) -> str | None:
    """Run the full pick -> agent -> validate -> PR -> merge flow for one issue.

    Returns the outcome string ('done' / 'failed' / 'dry-run' / 'skipped'), or
    None when the queue is empty.
    """
    cfg = config.load()

    # 1. Pro-window headroom.
    est = claude_session.estimate()
    if not _headroom_ok(est, mode):
        _log(f"Pro headroom too low (used {est.used}/{est.limit}) — bailing.")
        return "skipped"

    # 2. Pick the oldest ready issue.
    try:
        queue = github_ops.list_ready_issues()
    except github_ops.GhError as exc:
        _log(f"could not read issue queue: {exc}")
        return "skipped"
    if not queue:
        _log(f"queue empty — nothing labelled {cfg.labels.ready}.")
        return None

    issue_summary = queue[0]
    number = issue_summary["number"]
    issue = github_ops.get_issue(number)
    title = issue.get("title", "").strip()
    _log(f"picked issue #{number}: {title}")

    run_id = _run_id(number)
    session_id = str(uuid.uuid4())
    cfg.ensure_log_dir()
    log_path = cfg.log_dir / f"{run_id}.log"
    started_at = _now_iso()
    start_ts = time.time()
    branch = f"agent/issue-{number}-{_slug(title)}"

    record = {
        "run_id": run_id,
        "issue_number": number,
        "issue_title": title,
        "session_id": session_id,
        "mode": mode,
        "dry_run": cfg.dry_run,
        "branch": branch,
        "started_at": started_at,
        "log_path": str(log_path),
    }

    def finish(outcome: str, detail: str, failed_gate: str | None = None) -> str:
        record.update(
            outcome=outcome,
            detail=detail,
            failed_gate=failed_gate,
            finished_at=_now_iso(),
            duration_sec=int(time.time() - start_ts),
        )
        _append_run_record(record)
        _log(f"issue #{number}: {outcome} — {detail}")
        return outcome

    # 3. Move to agent-running and cut the branch.
    try:
        github_ops.set_agent_label(number, cfg.labels.running)
        github_ops.create_branch(branch)
    except (github_ops.GhError, github_ops.GitError) as exc:
        return finish("failed", f"setup failed: {exc}")

    # 4 + 5. Run claude headless against a UUID-keyed named session.
    agent_name = f"AO #{number}: {title}"[:60]
    result = runner.run_agent(
        runner.compose_prompt(issue), session_id, run_id, log_path, issue, mode,
        name=agent_name,
    )
    if result.timed_out:
        _leave_for_inspection(number, branch, log_path, session_id,
                              f"Agent hit the {cfg.run_cap_minutes}-min run cap.")
        return finish("failed", "agent timed out (run cap)", failed_gate="run-cap")
    if not result.completed or result.exit_code != 0:
        _leave_for_inspection(number, branch, log_path, session_id,
                              f"`claude` exited {result.exit_code}.")
        return finish("failed", f"agent exited {result.exit_code}", failed_gate="agent")

    # 6. Validation gates — with resume-based auto-retry. On a failed gate the
    #    agent's own session is resumed with the error so it can self-correct.
    validation = validator.run_gates(log_path)
    attempt = 0
    while not validation.passed and attempt < cfg.max_retries:
        attempt += 1
        _log(f"gate '{validation.failed_gate}' failed — retry {attempt}/{cfg.max_retries} "
             f"(resuming session {session_id})")
        retry = runner.run_agent(
            runner.compose_retry_prompt(validation.failed_gate, validation.tail),
            session_id, run_id, log_path, issue, mode, resume=True,
        )
        if retry.timed_out or not retry.completed or retry.exit_code != 0:
            _leave_for_inspection(number, branch, log_path, session_id,
                                  f"Agent failed during retry {attempt} "
                                  f"(timed_out={retry.timed_out}, exit={retry.exit_code}).")
            return finish("failed", f"retry {attempt} failed", failed_gate=validation.failed_gate)
        validation = validator.run_gates(log_path)

    if not validation.passed:
        _leave_for_inspection(number, branch, log_path, session_id,
                              f"Validation gate **{validation.failed_gate}** still failing "
                              f"after {cfg.max_retries} retries."
                              f"\n\n```\n{validation.tail}\n```")
        return finish("failed", f"{validation.summary} after {attempt} retries",
                      failed_gate=validation.failed_gate)
    if attempt:
        _log(f"gates green after {attempt} retr{'y' if attempt == 1 else 'ies'}")

    # 7. Diff must be non-empty. An empty diff usually means the agent was
    #    unsure what to do — route it to needs-info, not failed.
    try:
        github_ops.fetch_base()
        if not github_ops.diff_has_changes(branch):
            _mark_needs_info(number, log_path, session_id)
            return finish("needs-info", "agent produced no changes — may need clarification",
                          failed_gate="diff")
        conflict = github_ops.has_merge_conflict(branch)
    except github_ops.GitError as exc:
        return finish("failed", f"git inspection failed: {exc}")

    # 8. Green: push, open PR, label done.
    retried = f" (after {attempt} retr{'y' if attempt == 1 else 'ies'})" if attempt else ""
    try:
        github_ops.push_branch(branch)
        pr_url = github_ops.create_pr(
            branch, _pr_title(branch, title), _pr_body(number, run_id, session_id, conflict))
        github_ops.set_agent_label(number, cfg.labels.done)
    except (github_ops.GhError, github_ops.GitError) as exc:
        return finish("failed", f"PR creation failed: {exc}")

    hint = "\n\n" + _resume_hint(session_id)

    if conflict:
        github_ops.comment(number, f"⚠️ Agent finished and all gates passed{retried}, but the branch "
                                   f"conflicts with `{cfg.base_branch}`. PR opened for manual "
                                   f"merge: {pr_url}{hint}")
        return finish("done", f"PR opened (merge conflict — needs human): {pr_url}")

    if cfg.dry_run:
        github_ops.comment(number, f"✅ Dry-run: all gates passed{retried}. "
                                   f"PR opened (not merged): {pr_url}{hint}")
        return finish("dry-run", f"would have merged PR: {pr_url}")

    # 8b. Live merge.
    try:
        github_ops.merge_pr(branch)
    except github_ops.GhError as exc:
        github_ops.comment(number, f"✅ Gates passed{retried}, PR opened, but auto-merge "
                                   f"failed: {exc}\n{pr_url}{hint}")
        return finish("done", f"PR opened, auto-merge failed: {pr_url}")
    github_ops.comment(number, f"✅ All gates passed{retried} — merged. {pr_url}")
    return finish("done", f"merged PR: {pr_url}")


def _headroom_ok(est: claude_session.Estimate, mode: str) -> bool:
    """Decide whether there is enough Pro headroom to start a run.

    burst: refuse on unknown, and require burst_min_headroom messages free.
    slow:  fire on unknown (one extra message will not bust the cap) and only
           bail when the window is provably depleted.
    """
    cfg = config.load()
    if mode == "burst":
        if not est.ok or est.headroom is None:
            return False
        return est.headroom >= cfg.burst_min_headroom
    # slow mode
    if not est.ok or est.headroom is None:
        return True
    return est.headroom > 0


def _pr_title(branch: str, issue_title: str) -> str:
    """Prefer the agent's conventional-commit subject; fall back to the issue."""
    try:
        subject = github_ops.git("log", "-1", "--format=%s", branch).strip()
    except github_ops.GitError:
        subject = ""
    return subject or issue_title


def _pr_body(number: int, run_id: str, session_id: str, conflict: bool) -> str:
    cfg = config.load()
    gate_names = ", ".join(g.name for g in cfg.gates) or "(no gates configured)"
    note = ""
    if conflict:
        note = "\n\n> ⚠️ This branch conflicts with the base branch — merge manually."
    return (
        f"Automated change by agents-orchestrator.\n\n"
        f"Closes #{number}\n\n"
        f"Run ID: `{run_id}` · agent session `{session_id}`\n"
        f"Full log under `{cfg.log_dir}/{run_id}.log`.\n"
        f"All local gates ({gate_names}) passed.{note}\n\n"
        f"🤖 Generated by agents-orchestrator"
    )


def _resume_hint(session_id: str) -> str:
    """A copy-paste line to open the agent's session and take it over.

    If CLAUDE_CONFIG_DIR is set in the orchestrator's env, surface it in the
    hint so the human gets a directly-pasteable line. Otherwise emit the plain
    form (claude defaults to ~/.claude).
    """
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    prefix = f"CLAUDE_CONFIG_DIR={cfg_dir} " if cfg_dir else ""
    return f"Open this agent: `{prefix}claude --resume {session_id}`"


def _leave_for_inspection(number: int, branch: str, log_path: Path,
                          session_id: str, detail: str) -> None:
    """Label the issue failed and leave the branch + a draft PR for a human.

    The branch is pushed (so partial work is never lost) and, when it has a
    real diff, a draft PR is opened so the human gets a diff view and a place
    to comment. All steps are best-effort — a failure to push must not mask
    the original failure.
    """
    cfg = config.load()
    github_ops.set_agent_label(number, cfg.labels.failed)
    pr_line = ""
    try:
        github_ops.fetch_base()
        has_diff = github_ops.diff_has_changes(branch)
        github_ops.push_branch(branch)
        if has_diff:
            pr_url = github_ops.create_pr(
                branch,
                _pr_title(branch, f"[failed] issue #{number}"),
                f"⚠️ Draft — agents-orchestrator's run on #{number} **failed validation**.\n\n"
                f"Opened for inspection only. Do not merge as-is.\n\nRefs #{number}",
                draft=True,
            )
            pr_line = f"\nDraft PR for inspection: {pr_url}"
    except (github_ops.GhError, github_ops.GitError) as exc:
        _log(f"could not push/PR failed branch {branch}: {exc}")
        pr_line = f"\n(could not open a draft PR: {exc})"

    github_ops.comment(
        number,
        f"❌ Agent run failed.\n\n{detail}\n\n"
        f"Branch `{branch}` left for inspection. Full log: `{log_path}`.{pr_line}\n\n"
        f"{_resume_hint(session_id)}",
    )


def _mark_needs_info(number: int, log_path: Path, session_id: str) -> None:
    """Route an issue to needs-info — the agent finished without a change.

    An empty diff usually means the agent could not tell what to do. Resuming
    the session shows its reasoning (and any "BLOCKED:" question it left).
    """
    cfg = config.load()
    github_ops.set_agent_label(number, cfg.labels.needs_info)
    github_ops.comment(
        number,
        f"❓ The agent ran but produced no code change — it likely needs "
        f"clarification. Re-label `{cfg.labels.ready}` once the issue is sharper.\n\n"
        f"Full log: `{log_path}`\n\n{_resume_hint(session_id)}",
    )


# --- mode drivers ---------------------------------------------------------

def run_slow() -> int:
    _log("slow mode — processing at most one issue.")
    outcome = process_one_issue("slow")
    if outcome is None:
        _log("done — queue was empty.")
    return 0


def run_burst() -> int:
    cfg = config.load()
    _log("burst mode — clearing the queue within safety caps.")
    deadline = time.time() + cfg.burst_max_hours * 3600
    processed = 0
    while True:
        if processed >= cfg.burst_max_issues:
            _log(f"burst stop: hit max issues ({cfg.burst_max_issues}).")
            break
        if time.time() >= deadline:
            _log(f"burst stop: hit wall-clock cap ({cfg.burst_max_hours}h).")
            break
        est = claude_session.estimate()
        if not _headroom_ok(est, "burst"):
            _log(f"burst stop: Pro headroom below {cfg.burst_min_headroom} "
                 f"(used {est.used}/{est.limit}).")
            break

        outcome = process_one_issue("burst")
        if outcome is None:
            _log("burst stop: queue empty.")
            break
        if outcome != "skipped":
            processed += 1
        else:
            # process_one bailed on headroom — stop rather than spin.
            break

    _log(f"burst finished — {processed} issue(s) processed.")
    return 0


def run_status() -> int:
    status.print_dashboard()
    return 0


_USAGE = """AGENTS-ORCHESTRATOR

Usage:
  orchestrator.py [slow]    Process one ready issue, then exit (default;
                            this is what launchd invokes).
  orchestrator.py burst     Loop through the queue until a safety cap trips
                            (queue empty / Pro headroom / time / issue count).
  orchestrator.py status    Print the status dashboard and exit.
  orchestrator.py --help    Show this message.

Per-project config lives in .agents-orchestrator.toml at the target repo's
root (see templates/.agents-orchestrator.toml.example).

Environment overrides (all optional):
  CLAUDE_CONFIG_DIR=...     Pick a Claude account dir (default ~/.claude).
                            Inherited into spawned `claude` subprocesses.
  AO_DRY_RUN=1              Do everything except the final merge.
  AO_PRO_LIMIT=45           Pro-plan message cap per 5h window.
  AO_RUN_CAP_MINUTES=30     Per-run wall-clock cap.
  AO_MAX_RETRIES=2          Resume-and-retry attempts on a failed gate.
  AO_BURST_MAX_ISSUES=5     Burst-mode issue cap.
  AO_BURST_MAX_HOURS=4      Burst-mode wall-clock cap.
  AO_BURST_MIN_HEADROOM=8   Min Pro messages free before the next burst run.
  AO_REPO_ROOT=...          Override the target repo working copy.
  AO_LOG_DIR=...            Override the log directory.
"""


def main(argv: list[str]) -> int:
    args = argv[1:]
    cmd = args[0] if args else "slow"

    if cmd in ("-h", "--help", "help"):
        print(_USAGE)
        return 0

    config.load().ensure_log_dir()
    if cmd == "slow":
        return run_slow()
    if cmd == "burst":
        return run_burst()
    if cmd == "status":
        return run_status()

    print(f"unknown subcommand: {cmd}\n", file=sys.stderr)
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
