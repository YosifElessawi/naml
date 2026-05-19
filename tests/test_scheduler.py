"""Tests for naml.scheduler — DAG ready set, work-stealing, overlap policy."""

from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from naml.scheduler import (
    OverlapPolicy,
    ScheduleError,
    Scheduler,
    effective_lane_count,
)

from tests.test_package import _build_sprint
from naml.package import load_sprint


class _TempMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-sched-")).resolve()

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)


class ReadyAndProgressTests(_TempMixin, unittest.TestCase):
    def _diamond(self):
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": []},
            {"id": "slice-2", "title": "B", "type": "AFK", "depends_on": ["slice-1"], "touches": []},
            {"id": "slice-3", "title": "C", "type": "AFK", "depends_on": ["slice-1"], "touches": []},
            {"id": "slice-4", "title": "D", "type": "AFK", "depends_on": ["slice-2", "slice-3"], "touches": []},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        return load_sprint(path)

    def test_initial_ready_set_excludes_dependents(self) -> None:
        sprint = self._diamond()
        sched = Scheduler(sprint)
        self.assertEqual(sched.ready_ids(), ["slice-1"])

    def test_progress_unlocks_dependents(self) -> None:
        sprint = self._diamond()
        sched = Scheduler(sprint)
        first = sched.pop_ready()
        self.assertEqual(first, "slice-1")
        sched.mark_done(first)
        self.assertEqual(sorted(sched.ready_ids()), ["slice-2", "slice-3"])
        # Drain mid-layer.
        self.assertIn(sched.pop_ready(), {"slice-2", "slice-3"})
        self.assertIn(sched.pop_ready(), {"slice-2", "slice-3"})
        self.assertIsNone(sched.pop_ready(blocking=False))
        # Mark them done.
        sched.mark_done("slice-2")
        sched.mark_done("slice-3")
        self.assertEqual(sched.ready_ids(), ["slice-4"])

    def test_failure_blocks_descendants(self) -> None:
        sprint = self._diamond()
        sched = Scheduler(sprint)
        sched.pop_ready()
        sched.mark_failed("slice-1")
        snap = sched.snapshot()
        self.assertEqual(snap["slice-1"], "failed")
        self.assertEqual(snap["slice-2"], "blocked_upstream")
        self.assertEqual(snap["slice-3"], "blocked_upstream")
        self.assertEqual(snap["slice-4"], "blocked_upstream")
        self.assertFalse(sched.has_remaining_work())

    def test_exhaustion(self) -> None:
        sprint = self._diamond()
        sched = Scheduler(sprint)
        ids: list[str] = []
        while sched.has_remaining_work():
            sid = sched.pop_ready(blocking=False)
            if sid is None:
                break
            ids.append(sid)
            sched.mark_done(sid)
        self.assertEqual(set(ids), {"slice-1", "slice-2", "slice-3", "slice-4"})
        self.assertTrue(sched.all_finished())


class OverlapPolicyTests(_TempMixin, unittest.TestCase):
    def _conflict_sprint(self):
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [],
             "touches": ["src/lib/foo.py"]},
            {"id": "slice-2", "title": "B", "type": "AFK", "depends_on": [],
             "touches": ["src/lib/**"]},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        return load_sprint(path)

    def test_strict_force_serial(self) -> None:
        sprint = self._conflict_sprint()
        sched = Scheduler(sprint, overlap_policy="strict")
        sched.preflight()
        # slice-1 is earlier in the manifest, so slice-2 gets forced to depend on it.
        self.assertEqual(sched.ready_ids(), ["slice-1"])
        self.assertEqual(len(sched.preflight_findings), 1)
        a, b, _glob, action = sched.preflight_findings[0]
        self.assertEqual((a, b), ("slice-1", "slice-2"))
        self.assertEqual(action, "serialised")
        # Progress.
        sched.pop_ready()
        sched.mark_done("slice-1")
        self.assertEqual(sched.ready_ids(), ["slice-2"])

    def test_abort_raises(self) -> None:
        sprint = self._conflict_sprint()
        sched = Scheduler(sprint, overlap_policy="abort")
        with self.assertRaises(ScheduleError) as ctx:
            sched.preflight()
        self.assertIn("touches overlap", str(ctx.exception))

    def test_warn_records_findings_no_dag_change(self) -> None:
        sprint = self._conflict_sprint()
        sched = Scheduler(sprint, overlap_policy="warn")
        sched.preflight()
        # Both still ready in parallel.
        self.assertEqual(sorted(sched.ready_ids()), ["slice-1", "slice-2"])
        self.assertEqual(len(sched.preflight_findings), 1)
        _a, _b, _glob, action = sched.preflight_findings[0]
        self.assertEqual(action, "warn")


class WorkStealingTests(_TempMixin, unittest.TestCase):
    """Multiple threads pulling from one scheduler share the work."""

    def test_three_workers_drain_layer(self) -> None:
        # Four independent slices, no deps. 3 workers should split them.
        slices = [
            {"id": f"slice-{i}", "title": f"S{i}", "type": "AFK",
             "depends_on": [], "touches": []}
            for i in range(1, 5)
        ]
        path = _build_sprint(self._tmp, slices=slices)
        sprint = load_sprint(path)
        sched = Scheduler(sprint)

        claimed_by_worker: dict[int, list[str]] = {1: [], 2: [], 3: []}
        lock = threading.Lock()

        def worker(idx: int) -> None:
            while True:
                sid = sched.pop_ready(blocking=False)
                if sid is None:
                    return
                with lock:
                    claimed_by_worker[idx].append(sid)
                sched.mark_done(sid)

        threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2, 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        all_claimed = sum(claimed_by_worker.values(), [])
        self.assertEqual(set(all_claimed), {"slice-1", "slice-2", "slice-3", "slice-4"})
        # No double-claims.
        self.assertEqual(len(all_claimed), 4)


class LaneCountTests(unittest.TestCase):
    def test_caps_at_dag_width(self) -> None:
        self.assertEqual(
            effective_lane_count(
                configured_default=5, hard_cap=8, dag_width=2
            ),
            2,
        )

    def test_caps_at_hard_cap(self) -> None:
        self.assertEqual(
            effective_lane_count(
                configured_default=10, hard_cap=4, dag_width=20
            ),
            4,
        )

    def test_uses_configured_when_smallest(self) -> None:
        self.assertEqual(
            effective_lane_count(
                configured_default=2, hard_cap=8, dag_width=10
            ),
            2,
        )

    def test_headroom_applies(self) -> None:
        self.assertEqual(
            effective_lane_count(
                configured_default=4, hard_cap=8, dag_width=10, headroom=1
            ),
            1,
        )

    def test_never_below_one(self) -> None:
        self.assertEqual(
            effective_lane_count(
                configured_default=0, hard_cap=0, dag_width=0
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
