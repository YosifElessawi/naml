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
