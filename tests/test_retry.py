"""Tests for ``naml retry <sprint-dir> <slice-id>``.

The retry subcommand resets one slice's persisted status so the next
``naml run`` will pick it up as ``pending`` again. Worktree + branch
are deliberately left intact (the user can inspect them via
``naml recover``).
"""

from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from naml import state as state_mod
from naml import states as states_mod
from naml.cli import main


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class _SprintRootMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-retry-")).resolve()
        self._sprint = self._tmp / "sprint-A"
        self._sprint.mkdir()
        state_mod.ensure_state_dir(self._sprint)

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_status(
        self,
        slice_id: str,
        *,
        state: str = states_mod.FAILED,
        attempts: dict[str, int] | None = None,
        last_error: str = "",
    ) -> state_mod.SliceStatus:
        status = state_mod.SliceStatus(
            slice_id=slice_id,
            state="pending",
            session_id="abc-123",
            branch=f"feat/{slice_id}",
            worktree=str(self._tmp / "wt" / slice_id),
            attempts=dict(attempts or {}),
            last_error=last_error,
        )
        status.record_transition(state="pending", detail="initial")
        status.record_transition(state=states_mod.WORK, detail="claude work")
        status.record_transition(state=state, detail=last_error or "")
        status.state = state
        state_mod.save_slice_status(self._sprint, status)
        return status


class RetrySuccessTests(_SprintRootMixin, unittest.TestCase):
    def test_resets_failed_slice(self) -> None:
        self._write_status(
            "slice-1",
            state=states_mod.FAILED,
            attempts={"work": 2},
            last_error="gate 'test' failed",
        )
        rc, out, err = _run("retry", str(self._sprint), "slice-1")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("slice-1", out)
        self.assertIn("pending", out)
        # New wording flags the session_id clear so users see why the
        # next naml run won't hit "Session ID already in use".
        self.assertIn("session_id", out)

        reloaded = state_mod.load_slice_status(self._sprint, "slice-1")
        assert reloaded is not None
        self.assertEqual(reloaded.state, states_mod.PENDING)
        self.assertEqual(reloaded.attempts, {})
        self.assertEqual(reloaded.last_error, "")
        # session_id is cleared so the next naml run mints a fresh one
        # (the persisted seed was "abc-123" — see _write_status).
        self.assertEqual(reloaded.session_id, "")

    def test_extends_transitions_with_retry_record(self) -> None:
        self._write_status("slice-1", state=states_mod.FAILED)
        rc, _, err = _run("retry", str(self._sprint), "slice-1")
        self.assertEqual(rc, 0, msg=err)

        reloaded = state_mod.load_slice_status(self._sprint, "slice-1")
        assert reloaded is not None
        # We seeded 3 transitions: pending, work, failed. Plus the retry record.
        self.assertEqual(len(reloaded.transitions), 4)
        # Earlier history is preserved.
        states_seen = [t.state for t in reloaded.transitions]
        self.assertEqual(
            states_seen,
            ["pending", states_mod.WORK, states_mod.FAILED, states_mod.PENDING],
        )
        # The new record carries an identifying detail.
        self.assertIn("retry", reloaded.transitions[-1].detail.lower())

    def test_retries_blocked_upstream_slice(self) -> None:
        self._write_status("slice-2", state=states_mod.BLOCKED_UPSTREAM)
        rc, _, err = _run("retry", str(self._sprint), "slice-2")
        self.assertEqual(rc, 0, msg=err)
        reloaded = state_mod.load_slice_status(self._sprint, "slice-2")
        assert reloaded is not None
        self.assertEqual(reloaded.state, states_mod.PENDING)

    def test_retries_merge_blocked_slice(self) -> None:
        self._write_status("slice-3", state=states_mod.MERGE_BLOCKED)
        rc, _, err = _run("retry", str(self._sprint), "slice-3")
        self.assertEqual(rc, 0, msg=err)


class RetryRefusalTests(_SprintRootMixin, unittest.TestCase):
    def test_refuses_non_failed_slice(self) -> None:
        self._write_status("slice-1", state=states_mod.PR)
        rc, _, err = _run("retry", str(self._sprint), "slice-1")
        self.assertEqual(rc, 1)
        self.assertIn("only failed", err)
        self.assertIn("pr", err)

    def test_refuses_merged_slice(self) -> None:
        self._write_status("slice-1", state=states_mod.MERGED)
        rc, _, err = _run("retry", str(self._sprint), "slice-1")
        self.assertEqual(rc, 1)
        self.assertIn("merged", err)

    def test_missing_slice_id_refuses(self) -> None:
        # No status file written; slice-99 does not exist on disk.
        rc, _, err = _run("retry", str(self._sprint), "slice-99")
        self.assertEqual(rc, 1)
        self.assertIn("no status file", err)
        self.assertIn("slice-99", err)

    def test_missing_sprint_dir_refuses(self) -> None:
        rc, _, err = _run(
            "retry", str(self._tmp / "nope"), "slice-1"
        )
        self.assertEqual(rc, 1)
        self.assertIn("not found", err)


if __name__ == "__main__":
    unittest.main()
