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
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import NamlConfig
from . import lane as lane_mod
from .package import Sprint
from .scheduler import (
    OverlapPolicy,
    ScheduleError,
    Scheduler,
    effective_lane_count,
)
from . import state as state_mod


log = logging.getLogger("naml.run")


_DEFAULT_LOG_ROOT = Path.home() / "Library" / "Logs" / "naml"


@dataclass
class RunReport:
    sprint_id: str
    lanes_effective: int
    per_slice_state: dict[str, str]
    aggregate_state: str            # complete | partial_failure | failed | awaiting_signoff
    overlap_findings: list[tuple[str, str, str, str]]


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

    Phase 2 semantics:
    - any failed / blocked_upstream → ``partial_failure`` (still "complete enough
      to inspect"); only ``failed`` if EVERY slice failed.
    - else if all slices reached ``pr`` (or further) → ``awaiting_signoff``.
    """
    if not per_slice:
        return "failed"
    failed = sum(1 for s in per_slice.values() if s in {"failed", "blocked_upstream"})
    if failed == len(per_slice):
        return "failed"
    if failed > 0:
        return "partial_failure"
    if stop_after == "pr":
        if all(s == "pr" for s in per_slice.values()):
            return "awaiting_signoff"
    # Phase 3 will extend this for review / merge.
    return "executing"


def run_sprint(
    sprint: Sprint,
    cfg: NamlConfig,
    *,
    overlap_policy: OverlapPolicy = "strict",
    lane_count: int | None = None,
    stop_after: str = "pr",
) -> RunReport:
    """Run a sprint to its Phase-2 terminal (all slices at ``pr``).

    Raises ``ScheduleError`` from pre-flight if ``overlap_policy='abort'``
    and the manifest declares overlapping independent slices. Otherwise
    returns a ``RunReport`` with per-slice + aggregate state.
    """
    if cfg.repo != sprint.target_repo:
        log.warning(
            "config repo %s != sprint target_repo %s — using sprint's value",
            cfg.repo, sprint.target_repo,
        )

    scheduler = Scheduler(sprint, overlap_policy=overlap_policy)
    scheduler.preflight()  # may raise ScheduleError under "abort"

    width = sprint.dag_width()
    lane_count_resolved = lane_count or effective_lane_count(
        configured_default=cfg.parallel_lanes_default,
        hard_cap=cfg.parallel_lanes_max,
        dag_width=width,
    )

    sprint_root = _resolve_sprint_root(cfg, sprint)
    state_mod.ensure_state_dir(sprint_root)
    sprint_state = state_mod.SprintState(
        sprint_id=sprint.id,
        state="executing",
        lanes_configured=cfg.parallel_lanes_default,
        lanes_effective=lane_count_resolved,
        slices={s.id: "pending" for s in sprint.slices},
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

            if terminal_state in {"failed"}:
                scheduler.mark_failed(slice_id)
            else:
                # Phase 2 treats "pr" as scheduler-done.
                scheduler.mark_done(slice_id)

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

    aggregate = _aggregate_state(per_slice, stop_after)
    sprint_state.state = aggregate
    sprint_state.slices = per_slice
    state_mod.save_sprint_state(sprint_root, sprint_state)

    return RunReport(
        sprint_id=sprint.id,
        lanes_effective=lane_count_resolved,
        per_slice_state=per_slice,
        aggregate_state=aggregate,
        overlap_findings=list(scheduler.preflight_findings),
    )
