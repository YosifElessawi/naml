"""Sprint runtime state — what's on disk under ``.naml/sprints/<id>/state/``.

Three artifact families:

- ``state/slice-<id>.summary.md``  — written by the implementer agent. Read
  by the orchestrator and injected into downstream slice prompts.
- ``state/slice-<id>.status.json`` — written by the lane worker. Records
  the per-slice state machine transitions for the dashboard + retries.
- ``state/sprint.json``            — written by the scheduler. Aggregate
  sprint state, the lane count chosen, and per-slice pointers.

Per-state timing is recorded in each status file's ``transitions`` list
(``[{state, entered_at, detail}, ...]``). Use ``status.duration_in(state)``
and ``status.entered_at(state)`` for derived metrics — those iterate the
list at call time rather than materialising a separate index.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


STATE_DIRNAME = "state"
SUMMARY_FILENAME = "{slice_id}.summary.md"
STATUS_FILENAME = "{slice_id}.status.json"
SPRINT_STATE_FILENAME = "sprint.json"


def _now_iso() -> str:
    """ISO 8601 UTC timestamp with microseconds, used for state transitions."""
    return datetime.now(tz=timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime | None:
    """Lenient ISO 8601 parse. Returns ``None`` on garbage so callers can
    skip a malformed entry without raising mid-run."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class Transition:
    """One state-machine transition. Append-only — never edited in place."""

    state: str
    entered_at: str           # ISO 8601 UTC (use _now_iso())
    detail: str = ""          # the same one-line detail _transition() emits

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def entered_dt(self) -> datetime | None:
        return _parse_iso(self.entered_at)


def _transitions_from(raw: Any) -> list[Transition]:
    """Tolerant decoder for the transitions array. Returns [] on absence
    or unexpected shapes — old state files load fine."""
    if not isinstance(raw, list):
        return []
    out: list[Transition] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        state = entry.get("state")
        entered_at = entry.get("entered_at")
        if not isinstance(state, str) or not isinstance(entered_at, str):
            continue
        out.append(
            Transition(
                state=state,
                entered_at=entered_at,
                detail=str(entry.get("detail", "")),
            )
        )
    return out


@dataclass
class SliceStatus:
    """Persisted per-slice state. Saved as JSON."""

    slice_id: str
    state: str = "pending"          # pending | setup | work | pr | review | merged | failed | needs_human_review | blocked_upstream
    session_id: str = ""            # implementer Claude session UUID
    branch: str = ""                # local branch name
    worktree: str = ""              # absolute path to the lane's worktree for this slice
    pr_url: str = ""                # set once the PR is open
    attempts: dict[str, int] = field(default_factory=dict)  # state -> retry count
    last_error: str = ""
    review_verdict: str = ""        # LGTM | REQUEST_CHANGES | ABANDON | UNKNOWN
    transitions: list[Transition] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def record_transition(self, *, state: str, detail: str = "") -> None:
        """Append a transition record. Caller is responsible for then
        flipping ``self.state`` to the new state value (the helper writes
        the timestamp; it does NOT touch ``self.state`` so callers can
        record the transition before or after the field flip as needed).
        """
        self.transitions.append(
            Transition(state=state, entered_at=_now_iso(), detail=detail)
        )

    # --- derived helpers ---------------------------------------------

    def entered_at(self, state: str) -> datetime | None:
        """Datetime of the FIRST entry into ``state``, or None if never entered."""
        for t in self.transitions:
            if t.state == state:
                return t.entered_dt
        return None

    def last_entered_at(self, state: str) -> datetime | None:
        """Datetime of the most recent entry into ``state``."""
        for t in reversed(self.transitions):
            if t.state == state:
                return t.entered_dt
        return None

    def duration_in(self, state: str) -> timedelta | None:
        """Cumulative time the slice spent in ``state`` across all entries.

        The duration of an entry is the gap to the NEXT transition. If the
        most recent entry into ``state`` has no successor yet, that segment
        is left out (slice is presumably still in it).
        """
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

    def total_duration(self) -> timedelta | None:
        """Wall-clock from first transition to last. None if <2 transitions."""
        if len(self.transitions) < 2:
            return None
        first = self.transitions[0].entered_dt
        last = self.transitions[-1].entered_dt
        if first is None or last is None:
            return None
        return last - first


@dataclass
class SprintState:
    """Aggregate sprint state. Mirrors per-slice ``SliceStatus.state`` rollup."""

    sprint_id: str
    state: str = "package_received"  # package_received | planning | executing | awaiting_signoff | merging | complete | failed | partial_failure
    lanes_configured: int = 0
    lanes_effective: int = 0
    slices: dict[str, str] = field(default_factory=dict)  # slice_id -> state
    transitions: list[Transition] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def record_transition(self, *, state: str, detail: str = "") -> None:
        self.transitions.append(
            Transition(state=state, entered_at=_now_iso(), detail=detail)
        )

    # --- derived helpers ---------------------------------------------

    def entered_at(self, state: str) -> datetime | None:
        for t in self.transitions:
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

    def total_duration(self) -> timedelta | None:
        if len(self.transitions) < 2:
            return None
        first = self.transitions[0].entered_dt
        last = self.transitions[-1].entered_dt
        if first is None or last is None:
            return None
        return last - first


# --- helpers -------------------------------------------------------------

def state_dir(sprint_root: Path) -> Path:
    return sprint_root / STATE_DIRNAME


def summary_path(sprint_root: Path, slice_id: str) -> Path:
    return state_dir(sprint_root) / SUMMARY_FILENAME.format(slice_id=slice_id)


def status_path(sprint_root: Path, slice_id: str) -> Path:
    return state_dir(sprint_root) / STATUS_FILENAME.format(slice_id=slice_id)


def sprint_state_path(sprint_root: Path) -> Path:
    return state_dir(sprint_root) / SPRINT_STATE_FILENAME


def ensure_state_dir(sprint_root: Path) -> None:
    state_dir(sprint_root).mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, body: str) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def load_slice_status(sprint_root: Path, slice_id: str) -> SliceStatus | None:
    p = status_path(sprint_root, slice_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return SliceStatus(
        slice_id=raw.get("slice_id", slice_id),
        state=raw.get("state", "pending"),
        session_id=raw.get("session_id", ""),
        branch=raw.get("branch", ""),
        worktree=raw.get("worktree", ""),
        pr_url=raw.get("pr_url", ""),
        attempts=dict(raw.get("attempts", {})),
        last_error=raw.get("last_error", ""),
        review_verdict=raw.get("review_verdict", ""),
        transitions=_transitions_from(raw.get("transitions")),
    )


def save_slice_status(sprint_root: Path, status: SliceStatus) -> None:
    ensure_state_dir(sprint_root)
    p = status_path(sprint_root, status.slice_id)
    _atomic_write_text(p, json.dumps(status.to_dict(), indent=2) + "\n")


def load_sprint_state(sprint_root: Path) -> SprintState | None:
    p = sprint_state_path(sprint_root)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return SprintState(
        sprint_id=raw.get("sprint_id", ""),
        state=raw.get("state", "package_received"),
        lanes_configured=int(raw.get("lanes_configured", 0)),
        lanes_effective=int(raw.get("lanes_effective", 0)),
        slices=dict(raw.get("slices", {})),
        transitions=_transitions_from(raw.get("transitions")),
    )


def save_sprint_state(sprint_root: Path, state: SprintState) -> None:
    ensure_state_dir(sprint_root)
    p = sprint_state_path(sprint_root)
    _atomic_write_text(p, json.dumps(state.to_dict(), indent=2) + "\n")


def read_summary(sprint_root: Path, slice_id: str) -> str:
    """Return the upstream summary body, or empty string if absent."""
    p = summary_path(sprint_root, slice_id)
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""
