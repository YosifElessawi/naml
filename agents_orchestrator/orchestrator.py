#!/usr/bin/env python3
"""agents-orchestrator — entry point and mode drivers.

Subcommands:
    slow     process at most one batch, then exit (default; launchd calls this)
    burst    loop until the queue / Pro window / time / count cap is hit
    status   print the dashboard and exit
    finish   merge PRs left open by an earlier stop_after={pr,review} run
    serve    run the local web cockpit on http://localhost:7777
    --help   show usage

`slow` and `burst` share the same inner pipeline; only the outer driver
differs. Within a batch, related issues share a Claude session for warm
cache + context, but each issue still gets its own branch and its own PR.
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


def _stage_lte(name: str, threshold: str) -> bool:
    """True if pipeline stage ``name`` is at or before ``threshold``.

    Used as: `if _stage_lte("pr", cfg.stop_after): proceed past pr`. Anything
    with a higher index than stop_after is skipped.
    """
    order = ["pr", "review", "merge"]
    return order.index(name) <= order.index(threshold)


# --- per-issue pipeline ---------------------------------------------------

def _process_issue(
    issue: dict, mode: str,
    *,
    shared_session_id: str | None = None,
    batch_id: str | None = None,
    batch_position: tuple[int, int] | None = None,
    prior_issues: list[dict] | None = None,
) -> tuple[str, str | None]:
    """Run the full pick -> agent -> validate -> PR -> review -> merge flow
    for one already-fetched issue.

    If ``shared_session_id`` is given, this is a batch continuation: resume
    that session with a batch-continuation prompt instead of starting fresh.

    Returns (outcome, session_id_used). Outcomes:
      done · failed · needs-info · stopped-at-pr · stopped-at-review
    """
    cfg = config.load()
    number = issue["number"]
    title = issue.get("title", "").strip()

    run_id = _run_id(number)
    session_id = shared_session_id or str(uuid.uuid4())
    cfg.ensure_log_dir()
    log_path = cfg.log_dir / f"{run_id}.log"
    started_at = _now_iso()
    start_ts = time.time()
    branch = f"agent/issue-{number}-{_slug(title)}"
    is_continuation = shared_session_id is not None

    record = {
        "run_id": run_id,
        "issue_number": number,
        "issue_title": title,
        "session_id": session_id,
        "mode": mode,
        "stop_after": cfg.stop_after,
        "branch": branch,
        "started_at": started_at,
        "log_path": str(log_path),
        "batch_id": batch_id,
        "batch_position": list(batch_position) if batch_position else None,
    }

    usage_acc = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_cost_usd": 0.0,
        "num_turns": 0,
    }

    def _add_usage(r: runner.RunnerResult) -> None:
        u = r.usage
        usage_acc["input_tokens"] += u.input_tokens
        usage_acc["output_tokens"] += u.output_tokens
        usage_acc["cache_creation_input_tokens"] += u.cache_creation_input_tokens
        usage_acc["cache_read_input_tokens"] += u.cache_read_input_tokens
        usage_acc["total_cost_usd"] += u.total_cost_usd
        usage_acc["num_turns"] += u.num_turns

    def finish(outcome: str, detail: str, failed_gate: str | None = None,
               pr_url: str | None = None) -> tuple[str, str]:
        usage_acc["total_cost_usd"] = round(usage_acc["total_cost_usd"], 6)
        record.update(
            outcome=outcome,
            detail=detail,
            failed_gate=failed_gate,
            finished_at=_now_iso(),
            duration_sec=int(time.time() - start_ts),
            usage=usage_acc,
            pr_url=pr_url,
        )
        _append_run_record(record)
        _log(f"issue #{number}: {outcome} — {detail}")
        return outcome, session_id

    # 1. Move to running and cut the branch.
    try:
        github_ops.set_agent_label(number, cfg.labels.running)
        github_ops.create_branch(branch)
    except (github_ops.GhError, github_ops.GitError) as exc:
        return finish("failed", f"setup failed: {exc}")

    # 2. Run the agent — fresh session, or resumed for batch continuation.
    agent_name = (f"AO batch:{batch_id}" if batch_id and not is_continuation
                  else f"AO #{number}: {title}")[:60]

    if is_continuation:
        assert batch_position is not None
        prompt = runner.compose_batch_continuation_prompt(
            issue, batch_position[0], batch_position[1], prior_issues or []
        )
    else:
        prompt = runner.compose_prompt(issue)

    result = runner.run_agent(
        prompt, session_id, run_id, log_path, issue, mode,
        name=None if is_continuation else agent_name,
        resume=is_continuation,
        batch_id=batch_id,
    )
    _add_usage(result)
    if result.timed_out:
        _leave_for_inspection(number, branch, log_path, session_id,
                              f"Agent hit the {cfg.run_cap_minutes}-min run cap.")
        return finish("failed", "agent timed out (run cap)", failed_gate="run-cap")
    if not result.completed or result.exit_code != 0:
        _leave_for_inspection(number, branch, log_path, session_id,
                              f"`claude` exited {result.exit_code}.")
        return finish("failed", f"agent exited {result.exit_code}", failed_gate="agent")

    # 3. Gates with resume-based auto-retry.
    validation = validator.run_gates(log_path)
    attempt = 0
    while not validation.passed and attempt < cfg.max_retries:
        attempt += 1
        _log(f"gate '{validation.failed_gate}' failed — retry {attempt}/{cfg.max_retries} "
             f"(resuming session {session_id})")
        retry = runner.run_agent(
            runner.compose_retry_prompt(validation.failed_gate, validation.tail),
            session_id, run_id, log_path, issue, mode, resume=True, batch_id=batch_id,
        )
        _add_usage(retry)
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

    # 4. Diff + conflict inspection.
    try:
        github_ops.fetch_base()
        if not github_ops.diff_has_changes(branch):
            _mark_needs_info(number, log_path, session_id)
            return finish("needs-info", "agent produced no changes — may need clarification",
                          failed_gate="diff")
        conflict = github_ops.has_merge_conflict(branch)
    except github_ops.GitError as exc:
        return finish("failed", f"git inspection failed: {exc}")

    # 5. Push + PR.
    retried = f" (after {attempt} retr{'y' if attempt == 1 else 'ies'})" if attempt else ""
    try:
        github_ops.push_branch(branch)
        pr_url = github_ops.create_pr(
            branch, _pr_title(branch, title), _pr_body(number, run_id, session_id, conflict, batch_id))
        github_ops.set_agent_label(number, cfg.labels.done)
    except (github_ops.GhError, github_ops.GitError) as exc:
        return finish("failed", f"PR creation failed: {exc}")

    hint = "\n\n" + _resume_hint(session_id)

    if conflict:
        github_ops.comment(number, f"⚠️ Agent finished and all gates passed{retried}, but the branch "
                                   f"conflicts with `{cfg.base_branch}`. PR opened for manual "
                                   f"merge: {pr_url}{hint}")
        return finish("done", f"PR opened (merge conflict — needs human): {pr_url}", pr_url=pr_url)

    # 6. Stop after PR if asked.
    if cfg.stop_after == "pr":
        github_ops.comment(number, f"✅ Gates passed{retried}. Stopped at `pr` stage — PR opened, "
                                   f"not merged: {pr_url}{hint}")
        return finish("stopped-at-pr", f"PR opened (stop_after=pr): {pr_url}", pr_url=pr_url)

    # 7. Optional auto-review.
    review_outcome: str | None = None
    if cfg.stop_after == "review" or cfg.auto_review:
        review_outcome = _run_auto_review(issue, pr_url, log_path, usage_acc)

    if cfg.stop_after == "review":
        verdict = review_outcome or "(no verdict)"
        github_ops.comment(number, f"✅ Gates passed{retried}. Auto-review posted "
                                   f"(verdict: {verdict}). Stopped at `review` stage — "
                                   f"not merged: {pr_url}{hint}")
        return finish("stopped-at-review",
                      f"PR opened + reviewed (stop_after=review): {pr_url}", pr_url=pr_url)

    # 8. Merge.
    try:
        github_ops.merge_pr(branch)
    except github_ops.GhError as exc:
        github_ops.comment(number, f"✅ Gates passed{retried}, PR opened, but auto-merge "
                                   f"failed: {exc}\n{pr_url}{hint}")
        return finish("done", f"PR opened, auto-merge failed: {pr_url}", pr_url=pr_url)
    github_ops.comment(number, f"✅ All gates passed{retried} — merged. {pr_url}")
    return finish("done", f"merged PR: {pr_url}", pr_url=pr_url)


def _run_auto_review(issue: dict, pr_url: str, log_path: Path, usage_acc: dict) -> str | None:
    """Spawn a fresh Claude session to review the PR. Posts a comment on the PR
    with the review text. Returns the verdict (``LGTM`` / ``REQUEST_CHANGES`` /
    ``None`` if it couldn't be parsed).

    Reviews run in a fresh session (not the implementing agent's) so the
    review is independent of the work.
    """
    number = issue["number"]
    _log(f"running auto-review on PR for issue #{number}")
    try:
        diff = github_ops.git("--no-pager", "diff",
                              f"origin/{config.load().base_branch}...HEAD")
    except github_ops.GitError as exc:
        _log(f"could not get diff for review: {exc}")
        return None

    # Cap diff size — Claude has a context limit, and gigantic diffs aren't
    # reviewable anyway.
    MAX_DIFF_CHARS = 80_000
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[…diff truncated for review…]"

    prompt = runner.compose_review_prompt(issue, pr_url, diff)
    review_result, text = runner.run_oneshot(prompt, log_path, label="REVIEW")

    # Add review's usage to the run's total.
    u = review_result.usage
    usage_acc["input_tokens"] += u.input_tokens
    usage_acc["output_tokens"] += u.output_tokens
    usage_acc["cache_creation_input_tokens"] += u.cache_creation_input_tokens
    usage_acc["cache_read_input_tokens"] += u.cache_read_input_tokens
    usage_acc["total_cost_usd"] += u.total_cost_usd
    usage_acc["num_turns"] += u.num_turns

    if not text.strip():
        _log("auto-review produced no text — skipping comment")
        return None

    verdict_match = re.search(r"VERDICT:\s*(LGTM|REQUEST_CHANGES)", text, re.IGNORECASE)
    verdict = verdict_match.group(1).upper() if verdict_match else None

    try:
        body = f"🤖 **Auto-review** by agents-orchestrator\n\n{text.strip()}"
        github_ops.comment(number, body)
    except github_ops.GhError as exc:
        _log(f"could not post review comment: {exc}")

    return verdict


# --- batch driver ---------------------------------------------------------

def process_one_batch(batch_id: str | None, issues: list[dict], mode: str) -> list[str]:
    """Process every issue in a batch under a single shared session.

    Returns a list of outcome strings, one per issue. The first issue starts
    a fresh session; each subsequent issue resumes it for warm cache +
    shared context (still one branch + one PR per issue).
    """
    cfg = config.load()
    outcomes: list[str] = []
    session_id: str | None = None
    prior_issues: list[dict] = []
    total = len(issues)

    for index, summary in enumerate(issues, start=1):
        # Re-fetch full issue body for each item — the queue listing only
        # carries number/title/createdAt/labels.
        try:
            issue = github_ops.get_issue(summary["number"])
        except github_ops.GhError as exc:
            _log(f"could not fetch issue #{summary['number']}: {exc}")
            outcomes.append("skipped")
            continue

        title = issue.get("title", "").strip()
        if batch_id:
            _log(f"batch:{batch_id} — issue {index}/{total}: #{issue['number']} {title}")
        else:
            _log(f"picked issue #{issue['number']}: {title}")

        outcome, used_session = _process_issue(
            issue, mode,
            shared_session_id=session_id,
            batch_id=batch_id,
            batch_position=(index, total) if total > 1 else None,
            prior_issues=list(prior_issues),
        )
        outcomes.append(outcome)
        if session_id is None:
            session_id = used_session
        prior_issues.append(issue)

        # If the agent hit a hard wall (failed/timed out) on issue 1, the
        # session is suspect — abandon the rest of the batch rather than
        # poisoning subsequent issues with broken context.
        if outcome == "failed" and index == 1:
            _log(f"batch:{batch_id} aborted — first issue failed; skipping remaining "
                 f"{total - index} issue(s)")
            break

    return outcomes


def _headroom_ok(est: claude_session.Estimate, mode: str) -> bool:
    """Whether there is enough Pro headroom to start a run."""
    cfg = config.load()
    if mode == "burst":
        if not est.ok or est.headroom is None:
            return False
        return est.headroom >= cfg.burst_min_headroom
    if not est.ok or est.headroom is None:
        return True
    return est.headroom > 0


# --- PR text + comments ---------------------------------------------------

def _pr_title(branch: str, issue_title: str) -> str:
    """Prefer the agent's conventional-commit subject; fall back to the issue."""
    try:
        subject = github_ops.git("log", "-1", "--format=%s", branch).strip()
    except github_ops.GitError:
        subject = ""
    return subject or issue_title


def _pr_body(number: int, run_id: str, session_id: str, conflict: bool,
             batch_id: str | None = None) -> str:
    cfg = config.load()
    gate_names = ", ".join(g.name for g in cfg.gates) or "(no gates configured)"
    note = ""
    if conflict:
        note = "\n\n> ⚠️ This branch conflicts with the base branch — merge manually."
    batch_line = f"\nBatch: `{batch_id}`" if batch_id else ""
    return (
        f"Automated change by agents-orchestrator.\n\n"
        f"Closes #{number}\n\n"
        f"Run ID: `{run_id}` · agent session `{session_id}`{batch_line}\n"
        f"Full log under `{cfg.log_dir}/{run_id}.log`.\n"
        f"All local gates ({gate_names}) passed.{note}\n\n"
        f"🤖 Generated by agents-orchestrator"
    )


def _resume_hint(session_id: str) -> str:
    """A copy-paste line to open the agent's session and take it over."""
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    prefix = f"CLAUDE_CONFIG_DIR={cfg_dir} " if cfg_dir else ""
    return f"Open this agent: `{prefix}claude --resume {session_id}`"


def _leave_for_inspection(number: int, branch: str, log_path: Path,
                          session_id: str, detail: str) -> None:
    """Label the issue failed and leave the branch + a draft PR for a human."""
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
    """Route an issue to needs-info — the agent finished without a change."""
    cfg = config.load()
    github_ops.set_agent_label(number, cfg.labels.needs_info)
    github_ops.comment(
        number,
        f"❓ The agent ran but produced no code change — it likely needs "
        f"clarification. Re-label `{cfg.labels.ready}` once the issue is sharper.\n\n"
        f"Full log: `{log_path}`\n\n{_resume_hint(session_id)}",
    )


# --- mode drivers ---------------------------------------------------------

def _next_batch() -> tuple[str | None, list[dict]] | None:
    """Return the next batch to process, or None on empty queue."""
    cfg = config.load()
    try:
        queue = github_ops.list_ready_issues()
    except github_ops.GhError as exc:
        _log(f"could not read issue queue: {exc}")
        return None
    if not queue:
        _log(f"queue empty — nothing labelled {cfg.labels.ready}.")
        return None
    batches = github_ops.group_into_batches(queue)
    return batches[0] if batches else None


def run_slow() -> int:
    cfg = config.load()
    _log(f"slow mode — processing at most one batch (stop_after={cfg.stop_after}).")

    est = claude_session.estimate()
    if not _headroom_ok(est, "slow"):
        _log(f"Pro headroom too low (used {est.used}/{est.limit}) — bailing.")
        return 0

    batch = _next_batch()
    if batch is None:
        return 0
    batch_id, issues = batch
    if batch_id:
        _log(f"batch:{batch_id} has {len(issues)} issue(s)")
    process_one_batch(batch_id, issues, "slow")
    return 0


def run_burst() -> int:
    cfg = config.load()
    _log(f"burst mode — clearing the queue within safety caps (stop_after={cfg.stop_after}).")
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

        batch = _next_batch()
        if batch is None:
            _log("burst stop: queue empty.")
            break
        batch_id, issues = batch
        if batch_id:
            _log(f"batch:{batch_id} has {len(issues)} issue(s)")
        outcomes = process_one_batch(batch_id, issues, "burst")
        # Count issues that actually ran (not skipped).
        processed += sum(1 for o in outcomes if o != "skipped")

    _log(f"burst finished — {processed} issue(s) processed.")
    return 0


def run_status() -> int:
    status.print_dashboard()
    return 0


def run_finish() -> int:
    """Merge PRs that were left at the `pr` or `review` stop point and now
    have a passing review.

    For each issue labelled `agent-done` with a PR open + mergeable: merge it.
    For each labelled `agent-done` but PR has un-resolved review requests:
    skip (human still has work to do).
    """
    cfg = config.load()
    _log("finish mode — looking for stopped-at-pr/review issues to merge.")
    try:
        issues = github_ops._gh_json([
            "issue", "list",
            "--label", cfg.labels.done,
            "--state", "open",
            "--json", "number,title,labels",
            "--limit", "100",
        ]) or []
    except github_ops.GhError as exc:
        _log(f"could not list done issues: {exc}")
        return 1

    if not issues:
        _log("no issues labelled agent-done.")
        return 0

    merged = 0
    for issue in issues:
        number = issue["number"]
        # Find the PR linked from this issue.
        try:
            prs = github_ops._gh_json([
                "pr", "list",
                "--state", "open",
                "--search", f"in:body Closes #{number}",
                "--json", "number,url,headRefName,mergeable,mergeStateStatus",
                "--limit", "5",
            ]) or []
        except github_ops.GhError as exc:
            _log(f"#{number}: could not find PR: {exc}")
            continue
        if not prs:
            _log(f"#{number}: no open PR found, skipping")
            continue
        pr = prs[0]
        branch = pr.get("headRefName", "")
        if pr.get("mergeable") == "CONFLICTING":
            _log(f"#{number}: PR #{pr['number']} conflicts — skipping (resolve manually)")
            continue
        try:
            github_ops.merge_pr(branch)
            github_ops.comment(number, f"✅ Finished — merged via `make finish`. {pr['url']}")
            _log(f"#{number}: merged {pr['url']}")
            merged += 1
        except github_ops.GhError as exc:
            _log(f"#{number}: merge failed: {exc}")
    _log(f"finish complete — {merged} PR(s) merged.")
    return 0


def run_serve(argv: list[str]) -> int:
    """Start the local web cockpit."""
    # Late import — only needed in serve mode. Match the package-vs-script
    # import shape at the top of this file.
    if __package__ in (None, ""):
        from agents_orchestrator import web
    else:
        from . import web
    port = 7777
    for arg in argv:
        if arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])
        elif arg == "--port" and argv.index(arg) + 1 < len(argv):
            port = int(argv[argv.index(arg) + 1])
    return web.serve(port=port)


_USAGE = """AGENTS-ORCHESTRATOR

Usage:
  orchestrator.py [slow]    Process one ready batch, then exit (default;
                            this is what launchd invokes).
  orchestrator.py burst     Loop through the queue until a safety cap trips
                            (queue empty / Pro headroom / time / issue count).
  orchestrator.py finish    Merge PRs left open by an earlier stop_after run.
  orchestrator.py status    Print the status dashboard and exit.
  orchestrator.py serve [--port 7777]
                            Start the local web cockpit.
  orchestrator.py --help    Show this message.

Per-project config lives in .agents-orchestrator.toml at the target repo's
root (see templates/.agents-orchestrator.toml.example).

Environment overrides (all optional):
  CLAUDE_CONFIG_DIR=...     Pick a Claude account dir (default ~/.claude).
                            Inherited into spawned `claude` subprocesses.
  AO_DRY_RUN=1              Shorthand for AO_STOP_AFTER=pr.
  AO_STOP_AFTER=pr|review|merge
                            Stop after the named pipeline stage.
  AO_AUTO_REVIEW=1          Run /review before merging (stop_after=merge only).
  AO_PRO_LIMIT=45           Pro-plan message cap per 5h window.
  AO_RUN_CAP_MINUTES=30     Per-run wall-clock cap.
  AO_MAX_RETRIES=2          Resume-and-retry attempts on a failed gate.
  AO_BURST_MAX_ISSUES=5     Burst-mode issue cap.
  AO_BURST_MAX_HOURS=4      Burst-mode wall-clock cap.
  AO_BURST_MIN_HEADROOM=8   Min Pro messages free before the next burst run.
  AO_BATCH_PREFIX=batch:    Issue-label prefix that groups a batch.
  AO_BATCH_MAX_SIZE=4       Max issues in one batched session.
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
    if cmd == "finish":
        return run_finish()
    if cmd == "serve":
        return run_serve(args[1:])

    print(f"unknown subcommand: {cmd}\n", file=sys.stderr)
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
