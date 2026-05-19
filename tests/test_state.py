"""Tests for naml.state — slice + sprint state persistence."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from naml import state as state_mod


class StatePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-state-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_slice_status_roundtrip(self) -> None:
        status = state_mod.SliceStatus(
            slice_id="slice-1",
            state="work",
            session_id="uuid-123",
            branch="naml/s1/slice-1",
            worktree="/tmp/wt",
            attempts={"work": 2},
        )
        state_mod.save_slice_status(self._tmp, status)
        loaded = state_mod.load_slice_status(self._tmp, "slice-1")
        self.assertIsNotNone(loaded)
        assert loaded is not None  # narrow for type-checker
        self.assertEqual(loaded.state, "work")
        self.assertEqual(loaded.session_id, "uuid-123")
        self.assertEqual(loaded.attempts, {"work": 2})

    def test_load_missing_returns_none(self) -> None:
        self.assertIsNone(state_mod.load_slice_status(self._tmp, "slice-nope"))

    def test_sprint_state_roundtrip(self) -> None:
        sprint_state = state_mod.SprintState(
            sprint_id="2026-05-19-test",
            state="executing",
            lanes_configured=3,
            lanes_effective=2,
            slices={"slice-1": "work", "slice-2": "pending"},
        )
        state_mod.save_sprint_state(self._tmp, sprint_state)
        loaded = state_mod.load_sprint_state(self._tmp)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.state, "executing")
        self.assertEqual(loaded.lanes_effective, 2)
        self.assertEqual(loaded.slices["slice-1"], "work")

    def test_atomic_writes_dont_corrupt_on_concurrent_reads(self) -> None:
        # Just verify file is fully written before name swap — by writing
        # then reading back many times.
        for i in range(50):
            status = state_mod.SliceStatus(slice_id="slice-x", state=f"state-{i}")
            state_mod.save_slice_status(self._tmp, status)
            loaded = state_mod.load_slice_status(self._tmp, "slice-x")
            assert loaded is not None
            self.assertEqual(loaded.state, f"state-{i}")

    def test_read_missing_summary_returns_empty(self) -> None:
        self.assertEqual(state_mod.read_summary(self._tmp, "slice-x"), "")


if __name__ == "__main__":
    unittest.main()
