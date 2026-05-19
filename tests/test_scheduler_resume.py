"""Tests for ``Scheduler.absorb_existing_statuses`` — resume awareness.

When ``naml run`` is invoked on a sprint that already has per-slice
status files (e.g. from a prior partial run), the scheduler must:

- Treat slices whose state is in ``LANE_DONE_STATES`` as already finished,
  releasing their dependents.
- Treat slices whose state is in ``LANE_FAILED_STATES`` as failed,
  blocking their descendants.
- Refuse to silently re-pop slices stuck in a mid-attempt state
  (``setup``, ``work``, ``pr``, ``review``, ``merging``…). Those get a
  stderr warning and are treated as failed so the user is forced to
  explicitly run ``naml retry``.
- Leave slices with no status file as pending — normal popping.
"""

from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from naml import state as state_mod
from naml import states as states_mod
from naml.package import load_sprint
from naml.scheduler import Scheduler

from tests.test_package import _build_sprint


def _diamond_path(root: Path) -> Path:
    """slice-1 → slice-2, slice-3 → slice-4 (a diamond)."""
    slices = [
        {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": []},
        {"id": "slice-2", "title": "B", "type": "AFK", "depends_on": ["slice-1"], "touches": []},
        {"id": "slice-3", "title": "C", "type": "AFK", "depends_on": ["slice-1"], "touches": []},
        {"id": "slice-4", "title": "D", "type": "AFK", "depends_on": ["slice-2", "slice-3"], "touches": []},
    ]
    return _build_sprint(root, slices=slices)


def _write_status(sprint_root: Path, slice_id: str, state: str) -> None:
    state_mod.ensure_state_dir(sprint_root)
    status = state_mod.SliceStatus(slice_id=slice_id, state=state)
    status.record_transition(state=state, detail="seeded by test")
    state_mod.save_slice_status(sprint_root, status)


class AbsorbExistingStatusesTests(unittest.TestCase):
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-sched-resume-")).resolve()
        self._sprint_root = _diamond_path(self._tmp)
        self._sprint = load_sprint(self._sprint_root)

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- happy paths -----------------------------------------------------

    def test_no_status_files_leaves_everything_pending(self) -> None:
        sched = Scheduler(self._sprint)
        sched.preflight()
        # Capture stderr — should be empty.
        err = io.StringIO()
        with redirect_stderr(err):
            sched.absorb_existing_statuses(self._sprint_root)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(sched.ready_ids(), ["slice-1"])
        # Pop slice-1 normally.
        self.assertEqual(sched.pop_ready(blocking=False), "slice-1")

    def test_merged_status_releases_dependents(self) -> None:
        _write_status(self._sprint_root, "slice-1", states_mod.MERGED)
        sched = Scheduler(self._sprint)
        sched.preflight()
        sched.absorb_existing_statuses(self._sprint_root)
        snap = sched.snapshot()
        self.assertEqual(snap["slice-1"], "done")
        # slice-2 and slice-3 (children of slice-1) are now ready.
        self.assertEqual(sorted(sched.ready_ids()), ["slice-2", "slice-3"])
        # slice-1 is never popped again.
        # Drain the ready set, then ensure slice-1 was not among them.
        popped: list[str] = []
        while True:
            sid = sched.pop_ready(blocking=False)
            if sid is None:
                break
            popped.append(sid)
            sched.mark_done(sid)
        self.assertNotIn("slice-1", popped)

    def test_review_passed_also_releases_dependents(self) -> None:
        # REVIEW_PASSED is in LANE_DONE_STATES too.
        _write_status(self._sprint_root, "slice-1", states_mod.REVIEW_PASSED)
        sched = Scheduler(self._sprint)
        sched.preflight()
        sched.absorb_existing_statuses(self._sprint_root)
        self.assertEqual(sched.slice_status("slice-1"), "done")
        self.assertEqual(sorted(sched.ready_ids()), ["slice-2", "slice-3"])

    def test_failed_status_blocks_dependents(self) -> None:
        _write_status(self._sprint_root, "slice-1", states_mod.FAILED)
        sched = Scheduler(self._sprint)
        sched.preflight()
        sched.absorb_existing_statuses(self._sprint_root)
        snap = sched.snapshot()
        self.assertEqual(snap["slice-1"], "failed")
        self.assertEqual(snap["slice-2"], "blocked_upstream")
        self.assertEqual(snap["slice-3"], "blocked_upstream")
        self.assertEqual(snap["slice-4"], "blocked_upstream")
        # And the scheduler has no more work.
        self.assertFalse(sched.has_remaining_work())

    def test_needs_human_review_treated_as_failed(self) -> None:
        # NEEDS_HUMAN_REVIEW is in LANE_FAILED_STATES.
        _write_status(self._sprint_root, "slice-1", states_mod.NEEDS_HUMAN_REVIEW)
        sched = Scheduler(self._sprint)
        sched.preflight()
        sched.absorb_existing_statuses(self._sprint_root)
        snap = sched.snapshot()
        self.assertEqual(snap["slice-1"], "failed")
        self.assertEqual(snap["slice-2"], "blocked_upstream")

    # --- in-flight (orchestrator-died) protection ------------------------

    def test_in_flight_state_warns_and_treats_as_failed(self) -> None:
        # state=work — orchestrator died mid-implementer.
        _write_status(self._sprint_root, "slice-1", states_mod.WORK)
        sched = Scheduler(self._sprint)
        sched.preflight()
        err = io.StringIO()
        with redirect_stderr(err):
            sched.absorb_existing_statuses(self._sprint_root)
        warning = err.getvalue()
        self.assertIn("slice-1", warning)
        self.assertIn("work", warning)
        self.assertIn("naml retry", warning)
        # Treated as failed for this run.
        snap = sched.snapshot()
        self.assertEqual(snap["slice-1"], "failed")
        self.assertEqual(snap["slice-2"], "blocked_upstream")
        self.assertEqual(snap["slice-3"], "blocked_upstream")

    def test_in_flight_setup_state_also_blocks(self) -> None:
        _write_status(self._sprint_root, "slice-1", states_mod.SETUP)
        sched = Scheduler(self._sprint)
        sched.preflight()
        err = io.StringIO()
        with redirect_stderr(err):
            sched.absorb_existing_statuses(self._sprint_root)
        self.assertIn("setup", err.getvalue())
        self.assertEqual(sched.slice_status("slice-1"), "failed")

    # --- mixed --------------------------------------------------------

    def test_mixed_merged_and_failed(self) -> None:
        # slice-1 merged → slice-2/slice-3 released.
        # slice-2 then failed → slice-4 blocked.
        # slice-3 still pending → can still run.
        _write_status(self._sprint_root, "slice-1", states_mod.MERGED)
        _write_status(self._sprint_root, "slice-2", states_mod.FAILED)
        sched = Scheduler(self._sprint)
        sched.preflight()
        sched.absorb_existing_statuses(self._sprint_root)
        snap = sched.snapshot()
        self.assertEqual(snap["slice-1"], "done")
        self.assertEqual(snap["slice-2"], "failed")
        # slice-3 was released by slice-1 being done, never blocked since
        # only slice-2's descendants are blocked.
        self.assertEqual(snap["slice-3"], "ready")
        # slice-4 depends on slice-2 → blocked.
        self.assertEqual(snap["slice-4"], "blocked_upstream")

    def test_pending_status_file_leaves_slice_runnable(self) -> None:
        # ``naml retry`` resets a slice to PENDING and saves the status
        # file. On the next ``naml run``, absorb sees PENDING and must
        # treat it the same as "no status file" — i.e. leave the lane to
        # pick it up. Anything else would deadlock the retry workflow.
        _write_status(self._sprint_root, "slice-1", states_mod.PENDING)
        sched = Scheduler(self._sprint)
        sched.preflight()
        err = io.StringIO()
        with redirect_stderr(err):
            sched.absorb_existing_statuses(self._sprint_root)
        # No warning, slice is runnable.
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(sched.ready_ids(), ["slice-1"])


if __name__ == "__main__":
    unittest.main()
