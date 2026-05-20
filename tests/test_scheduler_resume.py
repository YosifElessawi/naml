"""Tests for ``Scheduler.absorb_existing_statuses`` — resume awareness.

When ``naml run`` is invoked on a sprint that already has per-slice
status files (e.g. from a prior partial run), the scheduler must:

- Treat slices whose state is in ``LANE_DONE_STATES`` as already finished,
  releasing their dependents.
- Treat slices whose state is in ``LANE_FAILED_STATES`` as failed,
  blocking their descendants.
- Auto-recover slices stuck in a mid-attempt state (``setup``, ``work``,
  ``pr``, ``review``, ``merging``…) by resetting them to ``pending`` on
  disk so the lane picks them up normally. The previous orchestrator is
  guaranteed dead — only one ``naml run`` owns a sprint at a time, and
  the SIGINT/SIGTERM handler in ``naml.run`` reaps Claude subprocesses
  before exit — so there is nothing to race with.
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

    # --- in-flight (orchestrator-died) auto-recovery ---------------------

    def test_in_flight_work_state_auto_recovers_to_pending(self) -> None:
        # state=work on disk — orchestrator died mid-implementer.
        # The scheduler should reset to PENDING on disk so the lane pops
        # it normally this run.
        _write_status(self._sprint_root, "slice-1", states_mod.WORK)
        sched = Scheduler(self._sprint)
        sched.preflight()
        err = io.StringIO()
        with redirect_stderr(err):
            sched.absorb_existing_statuses(self._sprint_root)
        warning = err.getvalue()
        self.assertIn("slice-1", warning)
        self.assertIn("orphan", warning.lower())
        # On-disk state was rewritten to pending.
        reloaded = state_mod.load_slice_status(self._sprint_root, "slice-1")
        self.assertIsNotNone(reloaded)
        assert reloaded is not None  # narrow for type checker
        self.assertEqual(reloaded.state, states_mod.PENDING)
        # In-memory scheduler state stays at its initial ``ready`` (no deps).
        self.assertEqual(sched.slice_status("slice-1"), "ready")
        # Dependents are not blocked — they'll be released when slice-1
        # actually completes this run.
        self.assertEqual(sched.slice_status("slice-2"), "pending")

    def test_in_flight_setup_state_also_recovers(self) -> None:
        _write_status(self._sprint_root, "slice-1", states_mod.SETUP)
        sched = Scheduler(self._sprint)
        sched.preflight()
        err = io.StringIO()
        with redirect_stderr(err):
            sched.absorb_existing_statuses(self._sprint_root)
        self.assertIn("orphan", err.getvalue().lower())
        reloaded = state_mod.load_slice_status(self._sprint_root, "slice-1")
        assert reloaded is not None
        self.assertEqual(reloaded.state, states_mod.PENDING)
        self.assertEqual(sched.slice_status("slice-1"), "ready")

    def test_orphan_recovery_clears_session_and_attempts(self) -> None:
        # Seed a status file that looks like a real mid-work orphan with
        # a session_id and a recorded attempt — the kind ``naml retry``
        # would normally clear.
        state_mod.ensure_state_dir(self._sprint_root)
        status = state_mod.SliceStatus(
            slice_id="slice-1",
            state=states_mod.WORK,
            session_id="abc-123-def",
            attempts={"work": 1},
            last_error="something old",
        )
        status.record_transition(state=states_mod.SETUP, detail="")
        status.record_transition(state=states_mod.WORK, detail="")
        state_mod.save_slice_status(self._sprint_root, status)

        sched = Scheduler(self._sprint)
        sched.preflight()
        with redirect_stderr(io.StringIO()):
            sched.absorb_existing_statuses(self._sprint_root)

        reloaded = state_mod.load_slice_status(self._sprint_root, "slice-1")
        assert reloaded is not None
        self.assertEqual(reloaded.state, states_mod.PENDING)
        # session_id wiped (Claude rejects re-used UUIDs)
        self.assertEqual(reloaded.session_id, "")
        # attempts cleared (full retry budget restored)
        self.assertEqual(reloaded.attempts, {})
        # last_error cleared
        self.assertEqual(reloaded.last_error, "")
        # transition row recorded so the timeline shows what happened
        last = reloaded.transitions[-1]
        self.assertEqual(last.state, states_mod.PENDING)
        self.assertIn("orphan", last.detail.lower())

    def test_orphan_recovery_is_idempotent_across_calls(self) -> None:
        # Two ``naml run`` invocations against the same orphan should both
        # succeed — the second call sees the slice already at PENDING and
        # treats it as the fresh-slate path (no extra orphan transitions).
        _write_status(self._sprint_root, "slice-1", states_mod.WORK)

        sched1 = Scheduler(self._sprint)
        sched1.preflight()
        with redirect_stderr(io.StringIO()):
            sched1.absorb_existing_statuses(self._sprint_root)
        after_first = state_mod.load_slice_status(self._sprint_root, "slice-1")
        assert after_first is not None
        n_transitions = len(after_first.transitions)

        sched2 = Scheduler(self._sprint)
        sched2.preflight()
        err = io.StringIO()
        with redirect_stderr(err):
            sched2.absorb_existing_statuses(self._sprint_root)
        # No new warning — slice is now pending, treated as fresh-slate.
        self.assertEqual(err.getvalue(), "")
        after_second = state_mod.load_slice_status(self._sprint_root, "slice-1")
        assert after_second is not None
        self.assertEqual(len(after_second.transitions), n_transitions)

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
