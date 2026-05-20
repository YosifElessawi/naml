"""Spawn Claude Code subprocesses for sprint slice work and review.

Two surfaces:

- ``run_implementer(prompt, session_id, log_path, cwd, cap_minutes)`` — the
  per-slice implementing agent (A1). First call writes the session UUID; a
  later ``resume_implementer`` re-enters it for fix loops.
- ``run_reviewer(prompt, log_path, cwd, cap_minutes)`` — fresh oneshot session
  for the reviewer agent (A2). Different allowlist (read-only). Returns the
  final text plus a parsed ``VERDICT:`` line when present.

Both inherit ``CLAUDE_CONFIG_DIR`` from the parent env. The naml process
should already be running under the right account before we spawn.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .tokens import TurnEmitter


_IMPLEMENTER_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Bash(*)",
    # User settings.json "ask" patterns commonly cover these — declare
    # both the no-space form (which beats a generic Bash(*) deny) and the
    # exact with-space match for the common user "ask" rule. Per-session
    # allowedTools beats settings.json ask when patterns match by shape.
    "Bash(git commit *)",
    "Bash(git commit*)",
    "Bash(git commit --*)",
    "Bash(git commit -*)",
    "Bash(git merge *)", "Bash(git merge*)",
    "Bash(git rebase *)", "Bash(git rebase*)",
    "Bash(git cherry-pick *)", "Bash(git cherry-pick*)",
    "Bash(git revert *)", "Bash(git revert*)",
    "Glob", "Grep",
    "WebFetch", "WebFetch(*)", "WebSearch",
    "Task", "TodoWrite",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskOutput", "TaskStop",
    "Skill", "ToolSearch",
    "AskUserQuestion",
    "ExitPlanMode", "EnterPlanMode",
    "EnterWorktree", "ExitWorktree",
)

_REVIEWER_ALLOWED_TOOLS: str = (
    "Read Glob Grep "
    "Bash(gh pr diff*) Bash(gh pr view*) "
    "Bash(git diff*) Bash(git log*) Bash(git show*) "
    "WebFetch WebSearch"
)


# Tier-3 merger agent gets write access (Edit, git add, git rebase --continue)
# but no push / merge / branch-flip surface. It's a one-shot — no session UUID.
_MERGER_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep",
    "Bash(*)",
    "Bash(git add *)", "Bash(git add*)",
    "Bash(git rebase --continue *)", "Bash(git rebase --continue)",
    "Bash(git rebase --continue*)",
    "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)",
    "Bash(git show*)",
    "Bash(git ls-files*)",
    # Lockfile / package manager ops the agent might need.
    "Bash(pnpm *)", "Bash(npm *)", "Bash(yarn *)",
    "Bash(uv *)", "Bash(pip *)", "Bash(pip3 *)",
)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    total_cost_usd: float = 0.0
    num_turns: int = 0


@dataclass(frozen=True)
class RunResult:
    completed: bool       # process exited on its own before the cap
    timed_out: bool       # killed because it hit the wall-clock cap
    exit_code: int        # -1 if killed
    usage: Usage
    final_text: str = ""  # populated for reviewer runs


def _claude_env(claude_config_dir: Path | None) -> dict[str, str]:
    """Build the env for a spawned `claude` subprocess.

    Claude Code's auth keychain entry is keyed by ``CLAUDE_CONFIG_DIR``.
    When the env var is unset, claude uses its default (``~/.claude``)
    AND looks up credentials stored against that "unset" key. Explicitly
    setting the env var to the same path uses a DIFFERENT keychain entry
    that does not exist → "Not logged in". So only set the env var when
    the config asks for a non-default directory.
    """
    env = os.environ.copy()
    if not claude_config_dir:
        return env
    default = (Path.home() / ".claude").resolve()
    requested = claude_config_dir.expanduser().resolve()
    if requested == default:
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = str(requested)
    return env


def _kill_group(proc: subprocess.Popen) -> None:
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


# Active-lane registry — every live Claude subprocess spawned by this naml
# process is recorded here while running. The SIGINT-twice ``hard pause``
# handler in ``naml.run`` iterates this set and ``_kill_group``s each entry.
# Lane workers see their subprocess die and write a ``failed`` status with
# ``last_error="paused mid-work (SIGINT)"`` via the normal post-wait path.
_active_procs_lock = threading.Lock()
_active_procs: set[subprocess.Popen] = set()


def _register_active_lane(proc: subprocess.Popen) -> None:
    with _active_procs_lock:
        _active_procs.add(proc)


def _unregister_active_lane(proc: subprocess.Popen) -> None:
    with _active_procs_lock:
        _active_procs.discard(proc)


def terminate_all_active_lanes() -> int:
    """SIGTERM (then SIGKILL) every Claude subprocess this naml process spawned.

    Returns the count of subprocesses signalled. Idempotent — already-dead
    procs are skipped by ``_kill_group``. Called by ``naml.run``'s
    SIGINT-twice handler for the hard-pause path.
    """
    with _active_procs_lock:
        snapshot = list(_active_procs)
    for proc in snapshot:
        _kill_group(proc)
    return len(snapshot)


def _parse_final_result(log_path: Path, start_offset: int) -> dict | None:
    """Return the final ``type=result`` event in the log slice, or None."""
    try:
        with log_path.open("rb") as fh:
            fh.seek(start_offset)
            last: dict | None = None
            for raw in fh:
                line = raw.strip()
                if not line or not line.startswith(b"{"):
                    continue
                try:
                    ev = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(ev, dict) and ev.get("type") == "result":
                    last = ev
            return last
    except OSError:
        return None


def _usage_from(ev: dict | None) -> Usage:
    if not ev:
        return Usage()
    u = ev.get("usage") or {}
    return Usage(
        input_tokens=int(u.get("input_tokens", 0) or 0),
        output_tokens=int(u.get("output_tokens", 0) or 0),
        cache_creation_input_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
        total_cost_usd=float(ev.get("total_cost_usd", 0.0) or 0.0),
        num_turns=int(ev.get("num_turns", 0) or 0),
    )


def _spawn(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    cap_minutes: int,
    header_label: str,
    emitter: TurnEmitter | None = None,
) -> tuple[subprocess.Popen, int, threading.Thread | None]:
    """Start a Claude subprocess. Tee stdout to the log + optional emitter.

    Returns ``(proc, log_start_offset, reader_thread)``. ``reader_thread`` is
    ``None`` unless an ``emitter`` was provided. When present, callers should
    ``join()`` it after the subprocess exits so the last bytes are flushed
    before final-result parsing reads from the log.

    Default path (no emitter): subprocess writes directly to the log file.
    Same behaviour the lane has always had. Used by reviewer / merger.

    Emitter path: we capture stdout via pipe and a dedicated thread re-writes
    each line to the log AND feeds it to the ``TurnEmitter`` so per-turn
    token / cost lines land in the slice JSONL as they happen — that's what
    makes the cockpit's cost timeline tick live instead of jumping at end.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start_offset = log_path.stat().st_size if log_path.exists() else 0

    # Write the banner first so it lands before any subprocess stdout.
    with log_path.open("a", encoding="utf-8") as banner:
        banner.write(f"\n{'=' * 60}\nCLAUDE {header_label} (cap {cap_minutes}m)\n{'=' * 60}\n")
        banner.flush()

    if emitter is None:
        with log_path.open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(  # noqa: S603 — argv constructed, not a shell string
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        _register_active_lane(proc)
        return proc, start_offset, None

    proc = subprocess.Popen(  # noqa: S603 — argv constructed, not a shell string
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        bufsize=1,           # line-buffered text mode
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _register_active_lane(proc)
    thread = threading.Thread(
        target=_tee_stdout,
        args=(proc.stdout, log_path, emitter),
        name=f"naml-tee-{header_label[:24]}",
        daemon=True,
    )
    thread.start()
    return proc, start_offset, thread


def _tee_stdout(
    stream: IO[str] | None, log_path: Path, emitter: TurnEmitter
) -> None:
    """Reader-thread loop: copy stdout to the log file + feed the emitter.

    Robust to:
    - The subprocess being killed (pipe closes, loop exits).
    - A single malformed line (``TurnEmitter.feed_line`` swallows that).
    - The log file becoming unwriteable mid-run (we log to stderr, keep
      feeding the emitter so cost data still flows).
    """
    if stream is None:
        return
    try:
        with log_path.open("a", encoding="utf-8") as log:
            for line in stream:
                try:
                    log.write(line)
                    log.flush()
                except OSError as exc:
                    print(
                        f"[naml.claude] tee write to {log_path} failed: {exc!s}",
                        file=sys.stderr,
                    )
                emitter.feed_line(line)
    except Exception as exc:  # noqa: BLE001 — last-line defence; thread must not crash silently
        print(f"[naml.claude] tee thread aborted: {exc!s}", file=sys.stderr)


# Grace period after we see a successful ``type:result`` event in the log
# before we SIGTERM the subprocess ourselves. Some Claude setups have Stop
# hooks (e.g. cmux) that take seconds to clean up after the agent's work is
# done; we let them finish if they can but refuse to wait the full run cap.
RESULT_GRACE_SECONDS = 45

# How often the log watcher polls for new bytes while waiting for the
# subprocess to exit OR for the success-result event to appear.
_POLL_INTERVAL_SECONDS = 0.5


def _scan_for_success_event(
    log_path: Path, start_offset: int, scan_offset: int
) -> tuple[bool, int]:
    """Scan log bytes since ``scan_offset`` for a ``type:result`` event.

    Returns ``(found_success, new_scan_offset)``. ``found_success`` is True
    iff a ``type:"result"`` event with ``is_error:false`` appears in the
    new bytes (subtype is informational — we treat any non-error result
    as completion, since some Claude versions emit ``subtype:"success"``
    while others omit subtype on success). The caller uses the new offset
    to avoid re-scanning bytes next iteration.
    """
    if not log_path.exists():
        return False, scan_offset
    try:
        size = log_path.stat().st_size
    except OSError:
        return False, scan_offset
    if size <= scan_offset:
        return False, scan_offset
    found = False
    try:
        with log_path.open("rb") as fh:
            fh.seek(scan_offset)
            for raw in fh:
                line = raw.strip()
                if not line or not line.startswith(b"{"):
                    continue
                try:
                    ev = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") != "result":
                    continue
                # Some result events report tool/subagent errors; only the
                # successful top-level result counts as completion.
                if ev.get("is_error") is True:
                    continue
                found = True
                # Don't break — pick up the latest in this chunk so any
                # later error event in the same window overrides.
            new_offset = size
    except OSError:
        new_offset = scan_offset
    return found, new_offset


def _wait_under_cap(
    proc: subprocess.Popen,
    *,
    log_path: Path,
    start_offset: int,
    cap_minutes: int,
    label: str,
) -> tuple[bool, bool, int]:
    """Wait for the subprocess under a two-tier cap.

    Returns ``(completed, timed_out, exit_code)``.

    Tier 1 — agent emits ``type:result`` (success). We treat that as logical
    completion. Give the subprocess ``RESULT_GRACE_SECONDS`` to drain its
    Stop hooks / background tasks. If it hasn't exited by then we
    ``SIGTERM`` the process group and report ``completed=True`` (logical
    success); the kill is bookkeeping.

    Tier 2 — wall-clock cap. If neither the result event nor a clean exit
    arrive before ``cap_minutes * 60`` seconds, we ``SIGTERM`` and report
    ``timed_out=True``.

    This replaces a naive ``proc.wait(timeout=cap)`` that mis-classified
    "agent done, subprocess waiting on hung Stop hook" as a timeout.
    """
    deadline = time.time() + cap_minutes * 60
    scan_offset = start_offset
    result_seen_at: float | None = None

    while True:
        # Cheap probe: did the subprocess exit on its own?
        try:
            rc = proc.wait(timeout=_POLL_INTERVAL_SECONDS)
            return True, False, rc
        except subprocess.TimeoutExpired:
            pass

        now = time.time()

        # Tier 1: did the agent already declare success in the log?
        if result_seen_at is None:
            seen, scan_offset = _scan_for_success_event(
                log_path, start_offset, scan_offset
            )
            if seen:
                result_seen_at = now
                with log_path.open("a", encoding="utf-8") as log:
                    log.write(
                        f"\n[naml] {label}: success result observed; "
                        f"waiting up to {RESULT_GRACE_SECONDS}s for subprocess exit\n"
                    )

        if result_seen_at is not None:
            if now - result_seen_at >= RESULT_GRACE_SECONDS:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write(
                        f"\n[naml] {label}: grace expired post-result — "
                        f"killing subprocess (treating as completed)\n"
                    )
                _kill_group(proc)
                return True, False, 0

        # Tier 2: wall-clock cap.
        if now >= deadline:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    f"\n[naml] {label} cap of {cap_minutes}m hit — killing agent\n"
                )
            _kill_group(proc)
            return False, True, -1


def run_implementer(
    *,
    prompt: str,
    session_id: str,
    log_path: Path,
    cwd: Path,
    claude_bin: str = "claude",
    claude_config_dir: Path | None = None,
    cap_minutes: int = 30,
    resume: bool = False,
    display_name: str | None = None,
    tokens_jsonl_path: Path | None = None,
    slice_id: str = "",
    model_context_max: int = 0,
) -> RunResult:
    """Run (or resume) the implementer agent inside a worktree.

    First call sets the session UUID via ``--session-id`` and an optional
    display name. ``resume=True`` re-enters that same UUID for a fix loop.

    When ``tokens_jsonl_path`` is set, per-turn ``usage`` blocks are parsed
    out of the stream-json stdout and appended to that JSONL as they happen
    (drives the cockpit's live cost timeline). Omitted in pure tests or when
    the lane doesn't have a sprint context, in which case the implementer
    falls back to the legacy "subprocess writes straight to log" path.
    """
    env = _claude_env(claude_config_dir)
    base_flags = [
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "bypassPermissions",
        "--dangerously-skip-permissions",
        "--allowedTools", " ".join(_IMPLEMENTER_ALLOWED_TOOLS),
    ]
    if resume:
        cmd = [claude_bin, "--resume", session_id, "-p", prompt, *base_flags]
        label = f"RESUME — session {session_id}"
    else:
        cmd = [claude_bin, "-p", prompt, "--session-id", session_id, *base_flags]
        if display_name:
            cmd[1:1] = ["--name", display_name]
        label = f"START — session {session_id}"

    emitter: TurnEmitter | None = None
    if tokens_jsonl_path is not None and slice_id:
        emitter = TurnEmitter(
            jsonl_path=tokens_jsonl_path,
            slice_id=slice_id,
            session_id=session_id,
            model_context_max=model_context_max or 200_000,
        )

    proc, start_offset, reader = _spawn(
        cmd,
        cwd=cwd,
        env=env,
        log_path=log_path,
        cap_minutes=cap_minutes,
        header_label=label,
        emitter=emitter,
    )
    try:
        completed, timed_out, exit_code = _wait_under_cap(
            proc,
            log_path=log_path,
            start_offset=start_offset,
            cap_minutes=cap_minutes,
            label=label,
        )
        # Drain the tee thread so the last bytes (incl. the final type:result
        # event) are in the log before we parse usage.
        if reader is not None:
            reader.join(timeout=5)
        usage = _usage_from(_parse_final_result(log_path, start_offset))
        return RunResult(
            completed=completed,
            timed_out=timed_out,
            exit_code=exit_code,
            usage=usage,
        )
    finally:
        _unregister_active_lane(proc)


def run_reviewer(
    *,
    prompt: str,
    log_path: Path,
    cwd: Path,
    claude_bin: str = "claude",
    claude_config_dir: Path | None = None,
    cap_minutes: int = 20,
) -> RunResult:
    """Run a fresh oneshot reviewer agent. Returns final text + usage.

    No ``--session-id`` is set: the reviewer must be a brand-new context
    every time (per the design doc — no self-review).
    """
    env = _claude_env(claude_config_dir)
    cmd = [
        claude_bin, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "bypassPermissions",
        "--dangerously-skip-permissions",
        "--allowedTools", _REVIEWER_ALLOWED_TOOLS,
    ]
    proc, start_offset, _ = _spawn(
        cmd,
        cwd=cwd,
        env=env,
        log_path=log_path,
        cap_minutes=cap_minutes,
        header_label="REVIEW (fresh session)",
    )
    try:
        completed, timed_out, exit_code = _wait_under_cap(
            proc,
            log_path=log_path,
            start_offset=start_offset,
            cap_minutes=cap_minutes,
            label="REVIEW",
        )
        final = _parse_final_result(log_path, start_offset)
        text = ""
        if final:
            raw = final.get("result")
            if isinstance(raw, str):
                text = raw
        return RunResult(
            completed=completed,
            timed_out=timed_out,
            exit_code=exit_code,
            usage=_usage_from(final),
            final_text=text,
        )
    finally:
        _unregister_active_lane(proc)


def run_merger(
    *,
    prompt: str,
    log_path: Path,
    cwd: Path,
    claude_bin: str = "claude",
    claude_config_dir: Path | None = None,
    cap_minutes: int = 20,
) -> RunResult:
    """Run a fresh Tier-3 merger agent. Returns final text + usage.

    Like ``run_reviewer`` (no ``--session-id`` so the agent never shares
    context with the implementer) but with the merger allowlist: it can
    Edit files, git add, and run ``git rebase --continue`` to resolve and
    advance the rebase. Naml runs gates + finalizes the merge afterward —
    the agent does NOT push or call ``gh pr merge``.
    """
    env = _claude_env(claude_config_dir)
    cmd = [
        claude_bin, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "bypassPermissions",
        "--dangerously-skip-permissions",
        "--allowedTools", " ".join(_MERGER_ALLOWED_TOOLS),
    ]
    proc, start_offset, _ = _spawn(
        cmd,
        cwd=cwd,
        env=env,
        log_path=log_path,
        cap_minutes=cap_minutes,
        header_label="MERGER (fresh session)",
    )
    try:
        completed, timed_out, exit_code = _wait_under_cap(
            proc,
            log_path=log_path,
            start_offset=start_offset,
            cap_minutes=cap_minutes,
            label="MERGER",
        )
        final = _parse_final_result(log_path, start_offset)
        text = ""
        if final:
            raw = final.get("result")
            if isinstance(raw, str):
                text = raw
        return RunResult(
            completed=completed,
            timed_out=timed_out,
            exit_code=exit_code,
            usage=_usage_from(final),
            final_text=text,
        )
    finally:
        _unregister_active_lane(proc)


def parse_verdict(text: str) -> str:
    """Pull the trailing ``VERDICT: <X>`` line out of a reviewer output.

    Returns one of {``LGTM``, ``REQUEST_CHANGES``, ``ABANDON``, ``UNKNOWN``}.
    """
    for line in reversed(text.splitlines()):
        stripped = line.strip().upper()
        if stripped.startswith("VERDICT:"):
            verdict = stripped.split(":", 1)[1].strip()
            if verdict in {"LGTM", "REQUEST_CHANGES", "ABANDON"}:
                return verdict
    return "UNKNOWN"
