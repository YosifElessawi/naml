"""Project-level runtime state — what's on disk at ``.naml/state.json``.

One file per repo (per naml project). Sits *above* the per-sprint state
under ``.naml/sprints/<id>/state/sprint.json`` and rolls multiple sprints
into a single "what is naml doing right now" view for the dashboard.

State machine:

    idle               -- no active sprint
    active             -- a sprint is running (any slice not yet terminal)
    awaiting_human     -- at least one slice is in ``merge_blocked``
    paused             -- explicitly paused by the operator

The transitions list is append-only and follows the same shape used by
``state.py`` (``Transition``) so the dashboard can render project,
sprint, and slice timelines with one renderer.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from naml.state import (
    Transition,
    _atomic_write_text,
    _now_iso,
    _transitions_from,
)


PROJECT_STATE_FILENAME = "state.json"

IDLE = "idle"
ACTIVE = "active"
AWAITING_HUMAN = "awaiting_human"
PAUSED = "paused"

PROJECT_STATES = frozenset({IDLE, ACTIVE, AWAITING_HUMAN, PAUSED})


@dataclass
class ProjectMetrics:
    """Cumulative counters across all sprints ever run in this project.

    Counters only ever go up. Reset by deleting ``.naml/state.json``.
    """

    sprints_started: int = 0
    sprints_completed: int = 0
    sprints_failed: int = 0
    slices_merged: int = 0
    slices_blocked: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectState:
    """Top-level project state. One per repo, stored at ``.naml/state.json``."""

    state: str = IDLE                                 # one of PROJECT_STATES
    current_sprint: str = ""                          # sprint id, or "" when idle
    queued_sprints: list[str] = field(default_factory=list)  # FIFO sprint ids
    metrics: ProjectMetrics = field(default_factory=ProjectMetrics)
    transitions: list[Transition] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "current_sprint": self.current_sprint,
            "queued_sprints": list(self.queued_sprints),
            "metrics": self.metrics.to_dict(),
            "transitions": [t.to_dict() for t in self.transitions],
        }

    # --- mutation ----------------------------------------------------

    def record_transition(self, *, state: str, detail: str = "") -> None:
        """Append a transition record. Caller is responsible for flipping
        ``self.state`` (helper writes the timestamp; same contract as
        ``SliceStatus.record_transition`` / ``SprintState.record_transition``).
        """
        if state not in PROJECT_STATES:
            raise ValueError(f"unknown project state: {state!r}")
        self.transitions.append(
            Transition(state=state, entered_at=_now_iso(), detail=detail)
        )

    # --- derived helpers ---------------------------------------------

    def entered_at(self, state: str) -> datetime | None:
        for t in self.transitions:
            if t.state == state:
                return t.entered_dt
        return None

    def last_entered_at(self, state: str) -> datetime | None:
        for t in reversed(self.transitions):
            if t.state == state:
                return t.entered_dt
        return None

    def duration_in(self, state: str) -> timedelta | None:
        total = timedelta(0)
        counted = False
        for i, t in enumerate(self.transitions):
            if t.state != state:
                continue
            next_at = (
                self.transitions[i + 1].entered_dt
                if i + 1 < len(self.transitions)
                else None
            )
            if next_at is None or t.entered_dt is None:
                continue
            total += next_at - t.entered_dt
            counted = True
        return total if counted else None


# --- helpers -------------------------------------------------------------

def project_state_path(naml_dir: Path) -> Path:
    """Resolve ``.naml/state.json`` given the ``.naml`` directory."""
    return naml_dir / PROJECT_STATE_FILENAME


def _metrics_from(raw: Any) -> ProjectMetrics:
    if not isinstance(raw, dict):
        return ProjectMetrics()
    return ProjectMetrics(
        sprints_started=int(raw.get("sprints_started", 0)),
        sprints_completed=int(raw.get("sprints_completed", 0)),
        sprints_failed=int(raw.get("sprints_failed", 0)),
        slices_merged=int(raw.get("slices_merged", 0)),
        slices_blocked=int(raw.get("slices_blocked", 0)),
    )


def load_project_state(naml_dir: Path) -> ProjectState:
    """Load ``.naml/state.json`` or return a fresh ``ProjectState`` if absent.

    Tolerant: malformed JSON or missing fields fall back to defaults so a
    botched file never blocks ``naml status`` or the API endpoint.
    """
    p = project_state_path(naml_dir)
    if not p.is_file():
        return ProjectState()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ProjectState()
    if not isinstance(raw, dict):
        return ProjectState()
    state_value = raw.get("state", IDLE)
    if state_value not in PROJECT_STATES:
        state_value = IDLE
    queued_raw = raw.get("queued_sprints", [])
    queued = (
        [str(x) for x in queued_raw if isinstance(x, str)]
        if isinstance(queued_raw, list)
        else []
    )
    return ProjectState(
        state=state_value,
        current_sprint=str(raw.get("current_sprint", "")),
        queued_sprints=queued,
        metrics=_metrics_from(raw.get("metrics")),
        transitions=_transitions_from(raw.get("transitions")),
    )


def save_project_state(naml_dir: Path, state: ProjectState) -> None:
    """Atomic write of the project state file. Creates ``.naml/`` if missing."""
    naml_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        project_state_path(naml_dir),
        json.dumps(state.to_dict(), indent=2) + "\n",
    )


# --- transition helpers ------------------------------------------------
#
# The orchestrator calls these at well-defined points. Each helper is
# idempotent in the no-op case: if the project is already in the right
# state for a given event, no extra transition row is appended.


def naml_dir_for(cfg: Any) -> Path:
    """Resolve ``<repo_root>/.naml`` for a loaded ``NamlConfig``.

    Project state lives at a fixed path regardless of where the user has
    customised ``sprints_dir`` — it's the project-level layer, not part of
    any particular sprints layout.
    """
    return Path(cfg.repo_root) / ".naml"


def on_sprint_start(naml_dir: Path, sprint_id: str) -> ProjectState:
    """``naml run`` is about to begin work on ``sprint_id``.

    Bumps the ``sprints_started`` counter only when this is a fresh sprint
    (not a resumed one). Transitions to ``active`` unless already active
    for the same sprint, in which case nothing is appended. When the
    previous state was ``paused`` for this same sprint, records the
    transition as a resume instead of a fresh start so the timeline reads
    correctly.
    """
    state = load_project_state(naml_dir)

    is_new_sprint = state.current_sprint != sprint_id
    is_resuming = state.state == PAUSED and not is_new_sprint
    if is_new_sprint:
        state.metrics.sprints_started += 1
        state.current_sprint = sprint_id

    if state.state != ACTIVE:
        state.state = ACTIVE
        detail = (
            f"sprint {sprint_id} resumed from pause"
            if is_resuming
            else f"sprint {sprint_id} started"
        )
        state.record_transition(state=ACTIVE, detail=detail)

    save_project_state(naml_dir, state)
    return state


def on_sprint_run_finished(
    naml_dir: Path,
    sprint_id: str,
    *,
    aggregate_state: str,
) -> ProjectState:
    """``naml run`` returned. Flip to ``awaiting_human`` on partial/full
    failure; otherwise stay ``active`` (the sprint is mid-pipeline,
    waiting for the merge phase)."""
    state = load_project_state(naml_dir)

    if aggregate_state in {"failed", "partial_failure"}:
        if state.state != AWAITING_HUMAN:
            state.state = AWAITING_HUMAN
            state.record_transition(
                state=AWAITING_HUMAN,
                detail=f"sprint {sprint_id} finished run with {aggregate_state}",
            )

    save_project_state(naml_dir, state)
    return state


def on_sprint_paused(
    naml_dir: Path,
    sprint_id: str,
    *,
    mode: str = "drain",
    detail: str = "",
) -> ProjectState:
    """``naml run`` was interrupted by SIGINT/SIGTERM. Flip to ``paused`` so
    the next ``naml run`` knows it's a resume.

    ``mode`` is ``"drain"`` when in-flight slices finished naturally,
    ``"forced"`` when SIGINT was hit twice and Claude subprocesses were
    SIGTERM'd mid-work.
    """
    state = load_project_state(naml_dir)
    if state.state != PAUSED:
        state.state = PAUSED
        state.record_transition(
            state=PAUSED,
            detail=detail or f"sprint {sprint_id} paused ({mode})",
        )
    save_project_state(naml_dir, state)
    return state


def on_sprint_merge_finished(
    naml_dir: Path,
    sprint_id: str,
    *,
    sprint_state: str,
    merged_count: int,
    blocked_count: int,
) -> ProjectState:
    """``naml merge`` returned. Roll metrics + transition based on the
    sprint-level outcome."""
    state = load_project_state(naml_dir)

    state.metrics.slices_merged += max(merged_count, 0)
    state.metrics.slices_blocked += max(blocked_count, 0)

    if sprint_state == "complete":
        state.metrics.sprints_completed += 1
        state.current_sprint = ""
        if state.state != IDLE:
            state.state = IDLE
            state.record_transition(
                state=IDLE,
                detail=f"sprint {sprint_id} complete",
            )
    elif sprint_state in {"merge_blocked", "partial_failure", "failed"}:
        if sprint_state == "failed":
            state.metrics.sprints_failed += 1
        if state.state != AWAITING_HUMAN:
            state.state = AWAITING_HUMAN
            state.record_transition(
                state=AWAITING_HUMAN,
                detail=f"sprint {sprint_id} finished merge with {sprint_state}",
            )

    save_project_state(naml_dir, state)
    return state


# --- hierarchy view ---------------------------------------------------
#
# Composes project + sprint + slice state into a single nested dict for
# both ``naml status`` and the (Phase 5 C5.3) ``/api/state`` endpoint.


def build_hierarchy(cfg: Any) -> dict[str, Any]:
    """Return the full nested {project, current_sprint, lanes} payload.

    Reads from disk on every call — cheap (a handful of small JSON files)
    and avoids any cache invalidation surface. Tolerates absent state
    files so dashboards work from the very first invocation.
    """
    # Local imports keep project_state importable in test environments
    # that don't have the full runtime wired up.
    from . import state as state_mod

    naml = naml_dir_for(cfg)
    project = load_project_state(naml)
    payload: dict[str, Any] = {
        "project": project.to_dict(),
        "current_sprint": None,
        "lanes": [],
    }

    sprint_id = project.current_sprint
    if not sprint_id:
        return payload

    sprint_root = Path(cfg.sprints_path) / sprint_id
    sprint_state = state_mod.load_sprint_state(sprint_root)
    if sprint_state is None:
        return payload

    payload["current_sprint"] = {
        "sprint_id": sprint_state.sprint_id,
        "state": sprint_state.state,
        "lanes_configured": sprint_state.lanes_configured,
        "lanes_effective": sprint_state.lanes_effective,
        "slices": [],
        "transitions": [t.to_dict() for t in sprint_state.transitions],
    }

    slices_payload: list[dict[str, Any]] = []
    for slice_id, _state_label in sprint_state.slices.items():
        status = state_mod.load_slice_status(sprint_root, slice_id)
        if status is None:
            slices_payload.append({"slice_id": slice_id, "state": "pending"})
            continue
        slices_payload.append(status.to_dict())
    payload["current_sprint"]["slices"] = slices_payload

    return payload
