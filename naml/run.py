"""Orchestrate a sprint run.

``run_sprint(sprint, cfg, ...)`` wires the scheduler to lane workers, blocks
until every slice reaches a terminal state, and writes the final sprint
state. Phase 2 stops every slice at ``pr`` (PR open, awaiting review);
Phase 3 will extend the per-slice machine through review and merge.

Threading model:

- One scheduler instance per sprint run.
- ``lane_count`` worker threads pull from the scheduler.
- Each thread runs ``naml.lane.process_slice`` in a loop until the scheduler
  is exhausted.

The threading interpretation respects v2's "work-stealing lanes": no slice
is pre-assigned to a lane, every lane consumes from the same ready set.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import claude as claude_mod
from .config import Gate, NamlConfig
from . import lane as lane_mod
from .package import Sprint
from . import project_state as project_state_mod
from .scheduler import (
    OverlapPolicy,
    ScheduleError,
    Scheduler,
    effective_lane_count,
)
from . import state as state_mod
from . import states


class GatePreflightError(RuntimeError):
    """Raised when one or more gate executables can't be resolved before any lane spawns."""


log = logging.getLogger("naml.run")


# --- pause / resume plumbing -------------------------------------------
#
# Signal model:
# - SIGINT once (Ctrl-C in the foreground terminal): set ``_pause_requested``,
#   call ``scheduler.shutdown()`` so workers blocked in ``pop_ready`` wake
#   up and exit cleanly. In-flight slices keep running their Claude
#   subprocess to its natural end — that's the drain.
# - SIGINT again, or SIGTERM at any point: set ``_kill_requested``,
#   ``terminate_all_active_lanes()`` to SIGTERM live Claude subprocesses.
#   Each lane's ``_wait_under_cap`` returns; the lane writes a ``failed``
#   status; the worker exits its loop on the next ``pop_ready``.
# - Either way, ``run_sprint`` finishes by writing project state ``paused``
#   instead of ``awaiting_human``. ``naml run`` against a paused project
#   logs "resumed from pause" and picks up exactly where it left off via
#   the existing ``Scheduler.absorb_existing_statuses`` flow.
#
# Claude subprocesses are spawned with ``start_new_session=True`` so they
# do NOT share a process group with naml — a terminal Ctrl-C reaches naml
# only, leaving the live Claude session untouched until naml decides what
# to do with it. That's the precondition that makes drain mode work.

_pause_requested = threading.Event()
_kill_requested = threading.Event()
_current_scheduler: Scheduler | None = None


def _handle_pause_signal(signum: int, _frame: object) -> None:
    """Top-level signal handler. Installed by ``run_sprint`` for SIGINT and
    SIGTERM; restored on exit so other commands (``naml status``,
    ``naml retry``) keep their default Python handling.
    """
    if signum == signal.SIGTERM or _pause_requested.is_set():
        # SIGTERM → hard pause immediately. Second SIGINT → escalate.
        if not _kill_requested.is_set():
            _kill_requested.set()
            log.warning(
                "naml: hard pause (signal %d) — terminating live Claude lanes; "
                "in-flight slices will be marked failed",
                signum,
            )
            try:
                n = claude_mod.terminate_all_active_lanes()
            except Exception:  # noqa: BLE001 — last-line defence; signal handler must not raise
                log.exception("terminate_all_active_lanes() failed")
                n = 0
            log.warning("naml: signalled %d active Claude subprocess(es)", n)
            sched = _current_scheduler
            if sched is not None and not sched.is_shutdown():
                sched.shutdown()
        return

    # First SIGINT — soft drain.
    _pause_requested.set()
    log.warning(
        "naml: pause requested (signal %d) — draining lanes. "
        "Ctrl-C again to force-stop (kills in-flight slices).",
        signum,
    )
    sched = _current_scheduler
    if sched is not None:
        sched.shutdown()


def _install_signal_handlers() -> tuple[object, object] | None:
    """Install pause handlers if we're on the main thread. Returns the
    previous (SIGINT, SIGTERM) handlers for later restoration, or ``None``
    when handlers can't be installed (signal.signal raises off the main
    thread, and some embedded environments reject it altogether)."""
    if threading.current_thread() is not threading.main_thread():
        return None
    try:
        prev_int = signal.signal(signal.SIGINT, _handle_pause_signal)
        prev_term = signal.signal(signal.SIGTERM, _handle_pause_signal)
    except (ValueError, OSError):
        return None
    return prev_int, prev_term


def _restore_signal_handlers(prev: tuple[object, object] | None) -> None:
    if prev is None:
        return
    prev_int, prev_term = prev
    try:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
    except (ValueError, OSError):
        pass


def _reset_pause_state() -> None:
    """Clear pause flags so a subsequent ``run_sprint`` in the same process
    starts clean. The CLI always exits after one run; only tests rely on this.
    """
    _pause_requested.clear()
    _kill_requested.clear()


_DEFAULT_LOG_ROOT = Path.home() / "Library" / "Logs" / "naml"


@dataclass
class RunReport:
    sprint_id: str
    lanes_effective: int
    per_slice_state: dict[str, str]
    aggregate_state: str            # complete | partial_failure | failed | awaiting_signoff | paused
    overlap_findings: list[tuple[str, str, str, str]]


def _gate_executable_resolvable(argv0: str) -> tuple[bool, bool]:
    """Decide whether a gate's argv[0] is plausibly runnable.

    Returns ``(ok, is_warning_only)``.

    Policy (gates run inside slice worktrees, not the orchestrator's cwd):
    - Basename (no path separators) → PATH lookup with ``shutil.which``.
    - Absolute path                 → must exist + be executable.
    - Relative path containing ``/`` → ambiguous; emit a warning, do NOT
      fail preflight. The orchestrator can't know the worktree layout
      up front, and many real configs use ``./.venv/bin/<x>`` which only
      resolves inside the slice worktree.
    """
    if not argv0:
        return False, False
    p = Path(argv0)
    if p.is_absolute():
        return os.access(argv0, os.X_OK) and Path(argv0).is_file(), False
    if "/" in argv0 or "\\" in argv0:
        # Relative path with a separator — can't be checked from here.
        return True, True
    return shutil.which(argv0) is not None, False


def preflight_gates(gates: Iterable[Gate]) -> None:
    """Verify each gate's ``argv[0]`` resolves to a runnable executable.

    Raises ``GatePreflightError`` with a message listing **every** unrunnable
    gate so the user can fix the config in one shot. Relative-path argv[0]s
    (containing a ``/``) are warned about via the module logger but never
    abort preflight — they may resolve only inside a slice worktree.
    """
    broken: list[Gate] = []
    for gate in gates:
        if not gate.argv:
            broken.append(gate)
            continue
        ok, warn_only = _gate_executable_resolvable(gate.argv[0])
        if warn_only:
            log.warning(
                "gate %r: argv[0]=%r is a relative path; runnability will be "
                "checked inside each slice worktree",
                gate.name, gate.argv[0],
            )
            continue
        if not ok:
            broken.append(gate)

    if not broken:
        return

    lines = ["the following gates cannot be run on this machine:"]
    for gate in broken:
        argv_repr = " ".join(gate.argv) if gate.argv else "<empty argv>"
        lines.append(f"  - {gate.name}: {argv_repr}")
    lines.append(
        "fix the [[gates]] argv in .naml/config.toml so each gate's first "
        "element resolves on PATH (e.g. 'python3' instead of 'python') or "
        "points to an existing absolute path."
    )
    raise GatePreflightError("\n".join(lines))


def _resolve_log_root(cfg: NamlConfig) -> Path:
    if cfg.log_dir:
        return cfg.log_dir
    safe_repo = cfg.repo.replace("/", "-") or "default"
    return _DEFAULT_LOG_ROOT / safe_repo


def _resolve_lane_root(cfg: NamlConfig) -> Path:
    return _resolve_log_root(cfg) / "worktrees"


def _resolve_sprint_root(cfg: NamlConfig, sprint: Sprint) -> Path:
    """The canonical on-disk sprint dir, anchored to the configured sprints path."""
    return cfg.sprints_path / sprint.id


def _aggregate_state(per_slice: dict[str, str], stop_after: str) -> str:
    """Roll per-slice states up to a sprint-level state.

    Rules:
    - Every slice in ``LANE_FAILED_STATES`` → ``failed``.
    - Any slice in ``LANE_FAILED_STATES`` → ``partial_failure``
      (the rest finished but human attention is needed somewhere).
    - All slices in ``LANE_DONE_STATES`` matching ``stop_after`` target →
      ``awaiting_signoff``.
    - Otherwise → ``executing`` (shouldn't be reached after run completes —
      means a slice is still in flight, which is a bug).
    """
    if not per_slice:
        return states.SPRINT_FAILED

    failed = sum(1 for s in per_slice.values() if s in states.LANE_FAILED_STATES)
    if failed == len(per_slice):
        return states.SPRINT_FAILED
    if failed > 0:
        return states.SPRINT_PARTIAL_FAILURE

    target_state = {
        "pr": states.PR,
        "review": states.REVIEW_PASSED,
        "merge": states.MERGED,
    }.get(stop_after, states.REVIEW_PASSED)

    # Acceptable terminal states for an awaiting_signoff sprint: the target
    # OR any further-along state.
    acceptable = {
        "pr": {states.PR, states.REVIEW_PASSED, states.MERGED},
        "review": {states.REVIEW_PASSED, states.MERGED},
        "merge": {states.MERGED},
    }.get(stop_after, {states.REVIEW_PASSED, states.MERGED})

    if all(s in acceptable for s in per_slice.values()):
        if stop_after == "merge":
            return states.SPRINT_COMPLETE
        return states.SPRINT_AWAITING_SIGNOFF

    return states.SPRINT_EXECUTING


def run_sprint(
    sprint: Sprint,
    cfg: NamlConfig,
    *,
    overlap_policy: OverlapPolicy = "strict",
    lane_count: int | None = None,
    stop_after: str = "review",
) -> RunReport:
    """Run a sprint to the requested ``stop_after`` terminal.

    Defaults to ``stop_after = "review"`` (Phase 3): every slice goes through
    setup → work → pr → review, the sprint ends in ``awaiting_signoff``
    once every slice has either passed review or escalated to
    needs_human_review. Pass ``stop_after = "pr"`` to halt before review.

    Raises ``ScheduleError`` from pre-flight if ``overlap_policy='abort'``
    and the manifest declares overlapping independent slices. Otherwise
    returns a ``RunReport`` with per-slice + aggregate state.
    """
    if cfg.repo != sprint.target_repo:
        log.warning(
            "config repo %s != sprint target_repo %s — using sprint's value",
            cfg.repo, sprint.target_repo,
        )

    # Fail fast before any lane worker spawns if a gate is misconfigured.
    # This is the single most common "wasted hour" failure mode (a missing
    # binary like ``python`` is only discovered on the first gate run, after
    # the agent has already done its work).
    preflight_gates(cfg.gates)

    project_naml = project_state_mod.naml_dir_for(cfg)
    project_state_mod.on_sprint_start(project_naml, sprint.id)

    scheduler = Scheduler(sprint, overlap_policy=overlap_policy)
    scheduler.preflight()  # may raise ScheduleError under "abort"

    sprint_root = _resolve_sprint_root(cfg, sprint)
    # Reconcile externally-merged PRs first. After a rescue, a slice may
    # be on disk as review_passed (or failed) while its PR is already
    # merged on GitHub — promote those to merged before the scheduler
    # absorbs statuses, so dependents see the right upstream state.
    from . import reconciler as _reconciler  # local: avoid import cycle
    _reconciler.reconcile_sprint(sprint, cfg, sprint_root)
    # Reconcile with any existing per-slice status files so a re-run does
    # not pop slices that already succeeded (or are mid-attempt). Must run
    # after preflight (forced edges already applied) and before lanes spawn.
    scheduler.absorb_existing_statuses(sprint_root)

    width = sprint.dag_width()
    lane_count_resolved = lane_count or effective_lane_count(
        configured_default=cfg.parallel_lanes_default,
        hard_cap=cfg.parallel_lanes_max,
        dag_width=width,
    )

    state_mod.ensure_state_dir(sprint_root)
    sprint_state = state_mod.SprintState(
        sprint_id=sprint.id,
        state=states.SPRINT_PACKAGE_RECEIVED,
        lanes_configured=cfg.parallel_lanes_default,
        lanes_effective=lane_count_resolved,
        slices={s.id: "pending" for s in sprint.slices},
    )
    sprint_state.record_transition(
        state=states.SPRINT_PACKAGE_RECEIVED,
        detail=f"lanes={lane_count_resolved}, slices={len(sprint.slices)}",
    )
    sprint_state.state = states.SPRINT_EXECUTING
    sprint_state.record_transition(
        state=states.SPRINT_EXECUTING,
        detail="DAG built, lanes spawning",
    )
    state_mod.save_sprint_state(sprint_root, sprint_state)

    log_root = _resolve_log_root(cfg)
    lane_root = _resolve_lane_root(cfg)
    ctx = lane_mod.LaneContext(
        config=cfg,
        sprint=sprint,
        sprint_root=sprint_root,
        lane_root=lane_root,
        log_dir=log_root / "slices",
        stop_after=stop_after,
        overlap_findings=list(scheduler.preflight_findings),
    )

    def _worker(lane_idx: int) -> None:
        log.info("lane %d started", lane_idx)
        while True:
            slice_id = scheduler.pop_ready(blocking=True)
            if slice_id is None:
                log.info("lane %d: queue drained", lane_idx)
                return
            log.info("lane %d: claimed %s", lane_idx, slice_id)
            try:
                terminal_state = lane_mod.process_slice(slice_id, ctx)
            except Exception:  # noqa: BLE001 — defence in depth
                log.exception("lane %d: %s raised", lane_idx, slice_id)
                terminal_state = "failed"

            # Update sprint-level slice state mirror.
            sprint_state.slices[slice_id] = terminal_state
            state_mod.save_sprint_state(sprint_root, sprint_state)

            if terminal_state in states.LANE_FAILED_STATES:
                scheduler.mark_failed(slice_id)
            else:
                # Anything in LANE_DONE_STATES releases dependents.
                scheduler.mark_done(slice_id)

    # Install SIGINT/SIGTERM handlers so Ctrl-C drains lanes cleanly and a
    # second Ctrl-C (or SIGTERM) escalates to a hard kill that SIGTERMs
    # live Claude subprocesses. Handlers are restored in the ``finally``
    # below so commands other than ``naml run`` keep default signal handling.
    global _current_scheduler  # noqa: PLW0603 — module-level handler state
    _reset_pause_state()
    prev_handlers = _install_signal_handlers()
    _current_scheduler = scheduler

    try:
        threads: list[threading.Thread] = []
        for i in range(lane_count_resolved):
            t = threading.Thread(target=_worker, args=(i + 1,), daemon=False,
                                 name=f"naml-lane-{i + 1}")
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        snapshot = scheduler.snapshot()
        per_slice: dict[str, str] = {}
        for sid in (s.id for s in sprint.slices):
            status = state_mod.load_slice_status(sprint_root, sid)
            per_slice[sid] = status.state if status else snapshot.get(sid, "pending")

        paused = _pause_requested.is_set()
        hard_paused = _kill_requested.is_set()

        if paused:
            # Don't roll an aggregate verdict — the sprint is incomplete by
            # design. Leave sprint_state.state at SPRINT_EXECUTING so the
            # next ``naml run`` resumes naturally; just record a "paused"
            # transition row on the sprint timeline for visibility.
            pause_mode = "forced" if hard_paused else "drain"
            sprint_state.record_transition(
                state=sprint_state.state,
                detail=f"run interrupted ({pause_mode} pause)",
            )
            sprint_state.slices = per_slice
            state_mod.save_sprint_state(sprint_root, sprint_state)
            aggregate = "paused"
            project_state_mod.on_sprint_paused(
                project_naml, sprint.id, mode=pause_mode
            )
        else:
            aggregate = _aggregate_state(per_slice, stop_after)
            if aggregate != sprint_state.state:
                sprint_state.state = aggregate
                ok_count = sum(
                    1 for s in per_slice.values() if s in states.LANE_DONE_STATES
                )
                fail_count = sum(
                    1 for s in per_slice.values() if s in states.LANE_FAILED_STATES
                )
                sprint_state.record_transition(
                    state=aggregate,
                    detail=f"{ok_count}/{len(per_slice)} slices clear; "
                           f"{fail_count} need attention",
                )
            sprint_state.slices = per_slice
            state_mod.save_sprint_state(sprint_root, sprint_state)
            project_state_mod.on_sprint_run_finished(
                project_naml, sprint.id, aggregate_state=aggregate
            )

        return RunReport(
            sprint_id=sprint.id,
            lanes_effective=lane_count_resolved,
            per_slice_state=per_slice,
            aggregate_state=aggregate,
            overlap_findings=list(scheduler.preflight_findings),
        )
    finally:
        _current_scheduler = None
        _restore_signal_handlers(prev_handlers)
