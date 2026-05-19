"""Sprint runtime state — what's on disk under ``.naml/sprints/<id>/state/``.

Three artifact families:

- ``state/slice-<id>.summary.md``  — written by the implementer agent. Read
  by the orchestrator and injected into downstream slice prompts.
- ``state/slice-<id>.status.json`` — written by the lane worker. Records
  the per-slice state machine transitions for the dashboard + retries.
- ``state/sprint.json``            — written by the scheduler. Aggregate
  sprint state, the lane count chosen, and per-slice pointers.

Phase 2 keeps the state surface thin: status JSON tracks just enough to
resume after a crash. Phase 3 expands ``slice-<id>.status.json`` with full
review verdicts and the implementer session UUID.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


STATE_DIRNAME = "state"
SUMMARY_FILENAME = "{slice_id}.summary.md"
STATUS_FILENAME = "{slice_id}.status.json"
SPRINT_STATE_FILENAME = "sprint.json"


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
    review_verdict: str = ""        # LGTM | REQUEST_CHANGES | ABANDON | UNKNOWN (Phase 3)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SprintState:
    """Aggregate sprint state. Mirrors per-slice ``SliceStatus.state`` rollup."""

    sprint_id: str
    state: str = "package_received"  # package_received | planning | executing | awaiting_signoff | merging | complete | failed | partial_failure
    lanes_configured: int = 0
    lanes_effective: int = 0
    slices: dict[str, str] = field(default_factory=dict)  # slice_id -> state

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
