"""DAG-driven scheduler for sprint slices.

Replaces v1's issue-queue scheduling. Input is a parsed ``Sprint`` from
``naml.package``; output is a stream of slice IDs handed to lane workers
as they finish prior work.

Phase 2 behaviour:

- Pre-flight overlap check (deterministic, no LLM). Two independent slices
  that share a ``touches:`` glob are forced into a serial edge in
  ``"strict"`` mode (the default) — i.e. the second one becomes dependent
  on the first. Use ``"abort"`` if you'd rather refuse to start, and
  ``"warn"`` to log + ignore (not recommended).
- DAG ready-set computation: a slice becomes ready when all of its
  ``depends_on`` predecessors have ``done`` status.
- Work-stealing: lane workers call ``pop_ready()`` to grab the next slice;
  no pre-assigned partitions.
- Thread-safe: ``threading.Lock`` around the state mutations so multiple
  lanes can call concurrently.
- Failure handling: marking a slice ``failed`` excludes its dependents
  from ever becoming ready — they're flagged as ``blocked_upstream``.

Slice status values:

- ``pending``           — declared but not yet ready
- ``ready``             — predecessors done; eligible for a lane to grab
- ``in_flight``         — claimed by a lane, work in progress
- ``done``              — finished successfully (per the lane worker)
- ``failed``            — finished unsuccessfully
- ``blocked_upstream``  — an ancestor failed; this slice will never run
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Literal

from .package import Slice, Sprint


SliceStatus = Literal[
    "pending", "ready", "in_flight", "done", "failed", "blocked_upstream"
]


OverlapPolicy = Literal["strict", "abort", "warn"]


class ScheduleError(RuntimeError):
    """Raised when the pre-flight overlap check refuses to start the sprint."""


@dataclass
class SliceSchedule:
    """Mutable per-slice state kept by the scheduler."""

    slice: Slice
    depends_on: list[str] = field(default_factory=list)
    status: SliceStatus = "pending"

    @property
    def id(self) -> str:
        return self.slice.id


class Scheduler:
    """Thread-safe DAG scheduler. Each instance manages one sprint run.

    Construct, then call ``preflight()`` to apply the overlap policy.
    Lane workers loop over ``pop_ready()`` / ``mark_done()`` / ``mark_failed()``.
    The scheduler is **exhausted** when ``has_remaining_work()`` is false.
    """

    def __init__(
        self,
        sprint: Sprint,
        *,
        overlap_policy: OverlapPolicy = "strict",
    ) -> None:
        self.sprint = sprint
        self.overlap_policy = overlap_policy
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

        # Build the working schedule out of the sprint's slices. The
        # depends_on list is mutable: pre-flight may add forced edges.
        self._slices: dict[str, SliceSchedule] = {
            s.id: SliceSchedule(slice=s, depends_on=list(s.depends_on))
            for s in sprint.slices
        }

        # Children adjacency, recomputed after pre-flight may amend edges.
        self._children: dict[str, list[str]] = {}
        self._refresh_children()

        # Initial ready set.
        self._mark_ready_initial()

        # Optional list of pre-flight findings (slice_a, slice_b, glob, action).
        self.preflight_findings: list[tuple[str, str, str, str]] = []

    # ----- inspection -----------------------------------------------------

    def slice_status(self, slice_id: str) -> SliceStatus:
        with self._lock:
            return self._slices[slice_id].status

    def snapshot(self) -> dict[str, SliceStatus]:
        with self._lock:
            return {sid: sch.status for sid, sch in self._slices.items()}

    def has_remaining_work(self) -> bool:
        """True iff at least one slice is still pending/ready/in_flight."""
        with self._lock:
            return any(
                s.status in {"pending", "ready", "in_flight"}
                for s in self._slices.values()
            )

    def all_finished(self) -> bool:
        with self._lock:
            return all(
                s.status in {"done", "failed", "blocked_upstream"}
                for s in self._slices.values()
            )

    def ready_ids(self) -> list[str]:
        with self._lock:
            return [sid for sid, s in self._slices.items() if s.status == "ready"]

    # ----- pre-flight -----------------------------------------------------

    def preflight(self) -> None:
        """Apply the overlap policy. Idempotent.

        - ``warn``: emit a finding for each conflict, don't modify the DAG.
        - ``strict``: add a forced serial edge for each conflict so the
          later slice depends on the earlier one (by manifest order).
        - ``abort``: raise ``ScheduleError`` listing every conflict.
        """
        warnings = self.sprint.overlap_warnings()
        if not warnings:
            return

        if self.overlap_policy == "abort":
            details = "\n".join(
                f"  - {a} ⇄ {b} (overlap: {glob})" for a, b, glob in warnings
            )
            raise ScheduleError(
                f"refusing to start: {len(warnings)} touches overlap(s) between "
                f"independent slices.\n{details}\n"
                f"Set overlap_policy='strict' to auto-serialise or fix the manifest."
            )

        # Establish manifest order so 'first' / 'second' are stable.
        order = {s.id: i for i, s in enumerate(self.sprint.slices)}
        for a_id, b_id, glob in warnings:
            if order[a_id] <= order[b_id]:
                earlier, later = a_id, b_id
            else:
                earlier, later = b_id, a_id

            action = "warn" if self.overlap_policy == "warn" else "serialised"
            self.preflight_findings.append((earlier, later, glob, action))

            if self.overlap_policy == "strict":
                self._slices[later].depends_on.append(earlier)

        if self.overlap_policy == "strict":
            self._refresh_children()
            # Re-run the initial ready-set in case the new edge demoted a
            # previously-ready slice. Done is idempotent.
            with self._lock:
                for sch in self._slices.values():
                    if sch.status == "ready" and sch.depends_on:
                        sch.status = "pending"
                self._reevaluate_ready_locked()

    # ----- queue API ------------------------------------------------------

    def pop_ready(self, *, blocking: bool = True, timeout: float | None = None) -> str | None:
        """Claim and return the next ready slice id, or ``None``.

        With ``blocking=True`` (default), waits on the condition variable
        until either a slice becomes ready or all work is finished. With
        ``blocking=False``, returns immediately.
        """
        with self._cond:
            while True:
                ready = [sid for sid, s in self._slices.items() if s.status == "ready"]
                if ready:
                    # Take the manifest-order earliest ready slice. Stable.
                    order = {s.id: i for i, s in enumerate(self.sprint.slices)}
                    ready.sort(key=lambda sid: order[sid])
                    chosen = ready[0]
                    self._slices[chosen].status = "in_flight"
                    return chosen
                if not self._has_pending_or_in_flight_locked():
                    return None
                if not blocking:
                    return None
                # Wait for state changes.
                self._cond.wait(timeout=timeout)

    def mark_done(self, slice_id: str) -> None:
        with self._cond:
            sch = self._slices[slice_id]
            if sch.status != "in_flight":
                raise RuntimeError(
                    f"mark_done({slice_id}): expected in_flight, got {sch.status}"
                )
            sch.status = "done"
            self._reevaluate_ready_locked()
            self._cond.notify_all()

    def mark_failed(self, slice_id: str) -> None:
        with self._cond:
            sch = self._slices[slice_id]
            if sch.status != "in_flight":
                raise RuntimeError(
                    f"mark_failed({slice_id}): expected in_flight, got {sch.status}"
                )
            sch.status = "failed"
            self._mark_descendants_blocked_locked(slice_id)
            self._cond.notify_all()

    # ----- internals ------------------------------------------------------

    def _refresh_children(self) -> None:
        children: dict[str, list[str]] = defaultdict(list)
        for sch in self._slices.values():
            for dep in sch.depends_on:
                children[dep].append(sch.id)
        self._children = dict(children)

    def _mark_ready_initial(self) -> None:
        with self._lock:
            for sch in self._slices.values():
                if not sch.depends_on:
                    sch.status = "ready"

    def _reevaluate_ready_locked(self) -> None:
        """Promote any 'pending' slice whose deps are all 'done' to 'ready'."""
        for sch in self._slices.values():
            if sch.status != "pending":
                continue
            if all(self._slices[d].status == "done" for d in sch.depends_on):
                sch.status = "ready"

    def _has_pending_or_in_flight_locked(self) -> bool:
        return any(
            s.status in {"pending", "ready", "in_flight"}
            for s in self._slices.values()
        )

    def _mark_descendants_blocked_locked(self, failed_id: str) -> None:
        """Block every transitive descendant of ``failed_id`` that is still pending."""
        stack = list(self._children.get(failed_id, []))
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            sch = self._slices[cur]
            if sch.status in {"pending", "ready"}:
                sch.status = "blocked_upstream"
            stack.extend(self._children.get(cur, []))


def effective_lane_count(
    *, configured_default: int, hard_cap: int, dag_width: int, headroom: int | None = None
) -> int:
    """Compute ``min(configured, dag_width, headroom, hard_cap)`` for lane count.

    Matches the design doc rule. ``headroom=None`` means "no headroom constraint".
    """
    candidates: list[int] = [configured_default, hard_cap, max(1, dag_width)]
    if headroom is not None:
        candidates.append(max(1, headroom))
    return max(1, min(candidates))


def topological_iter(sprint: Sprint) -> Iterable[str]:
    """Convenience: yield slice ids in topological order."""
    yield from sprint.topological_order()
