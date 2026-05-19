"""Tests for ``naml recover <sprint-dir> <slice-id>``.

Recover is read-only and best-effort: it surfaces the worktree path,
branch, PR URL, commits since the base branch, and a pointer to the
agent's summary file. The commits listing exercises a real ``git log``
in a temp repo (see tests/test_worktree_symlinks.py for the same
fake-repo pattern).
"""

from __future__ import annotations

import io
import shutil
import subprocess
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


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class _SprintRootMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-recover-")).resolve()
        self._sprint = self._tmp / "sprint-A"
        self._sprint.mkdir()
        state_mod.ensure_state_dir(self._sprint)

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_repo_with_branch(
        self,
        branch: str = "feat/slice-1",
        extra_commits: int = 2,
    ) -> Path:
        """Create a tiny git repo on ``main`` then branch + commit. Returns
        the working-tree path the agent would have used."""
        repo = self._tmp / "wt-slice-1"
        repo.mkdir()
        _git("init", "-q", "-b", "main", cwd=repo)
        _git("config", "user.email", "test@example.com", cwd=repo)
        _git("config", "user.name", "Test", cwd=repo)
        (repo / "README.md").write_text("hi\n")
        _git("add", ".", cwd=repo)
        _git("commit", "-q", "-m", "init", cwd=repo)
        _git("checkout", "-q", "-b", branch, cwd=repo)
        for i in range(extra_commits):
            (repo / f"f{i}.txt").write_text(f"{i}\n")
            _git("add", ".", cwd=repo)
            _git("commit", "-q", "-m", f"feat: add f{i}.txt", cwd=repo)
        return repo

    def _write_status(
        self,
        slice_id: str,
        *,
        state: str = states_mod.FAILED,
        branch: str = "feat/slice-1",
        worktree: str = "",
        pr_url: str = "",
        last_error: str = "",
        attempts: dict[str, int] | None = None,
        session_id: str = "sess-uuid-abc",
    ) -> state_mod.SliceStatus:
        status = state_mod.SliceStatus(
            slice_id=slice_id,
            state=state,
            session_id=session_id,
            branch=branch,
            worktree=worktree,
            pr_url=pr_url,
            attempts=dict(attempts or {}),
            last_error=last_error,
        )
        status.record_transition(state="pending", detail="initial")
        status.record_transition(state=state, detail=last_error or "")
        state_mod.save_slice_status(self._sprint, status)
        return status


class RecoverHappyPathTests(_SprintRootMixin, unittest.TestCase):
    def test_prints_fields_and_commits_with_real_worktree(self) -> None:
        repo = self._make_repo_with_branch(extra_commits=2)
        self._write_status(
            "slice-1",
            state=states_mod.FAILED,
            branch="feat/slice-1",
            worktree=str(repo),
            pr_url="https://github.com/owner/repo/pull/42",
            last_error="gate 'test' failed: pytest not found",
            attempts={"work": 2},
        )

        rc, out, err = _run("recover", str(self._sprint), "slice-1")
        self.assertEqual(rc, 0, msg=err)

        # Header.
        self.assertIn("slice-1", out)
        self.assertIn("state=failed", out)
        # Fields.
        self.assertIn(str(repo), out)
        self.assertIn("feat/slice-1", out)
        self.assertIn("https://github.com/owner/repo/pull/42", out)
        self.assertIn("gate 'test' failed", out)
        self.assertIn("sess-uuid-abc", out)
        self.assertIn("work", out)  # attempts JSON
        # Real git log produced two commits.
        self.assertIn("Commits on this branch", out)
        self.assertIn("feat: add f0.txt", out)
        self.assertIn("feat: add f1.txt", out)

    def test_omits_optional_lines_when_empty(self) -> None:
        repo = self._make_repo_with_branch(extra_commits=0)
        self._write_status(
            "slice-2",
            state=states_mod.FAILED,
            branch="feat/slice-2",
            worktree=str(repo),
            pr_url="",
            last_error="",
            attempts={},
        )
        # Make the branch exist on disk; the helper used "feat/slice-1".
        # Re-create with the right name:
        _git("checkout", "-q", "-b", "feat/slice-2", cwd=repo)
        rc, out, err = _run("recover", str(self._sprint), "slice-2")
        self.assertEqual(rc, 0, msg=err)
        # No PR / last_error / attempts lines.
        self.assertNotIn("pr_url:", out)
        self.assertNotIn("last_error:", out)
        self.assertNotIn("attempts:", out)
        # No commits on the branch.
        self.assertIn("(no commits on this branch)", out)

    def test_lists_summary_file_when_present(self) -> None:
        repo = self._make_repo_with_branch()
        self._write_status(
            "slice-1",
            state=states_mod.FAILED,
            worktree=str(repo),
        )
        # Drop a summary file that the implementer agent would have written.
        summary_p = state_mod.summary_path(self._sprint, "slice-1")
        summary_p.write_text("# slice-1 summary\n\nline two\nline three\n")
        rc, out, err = _run("recover", str(self._sprint), "slice-1")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("Summary written by the agent", out)
        self.assertIn("slice-1.summary.md", out)
        self.assertIn("4 lines", out)


class RecoverWorktreeMissingTests(_SprintRootMixin, unittest.TestCase):
    def test_handles_deleted_worktree(self) -> None:
        # Record a worktree path that doesn't exist.
        ghost = self._tmp / "wt-ghost"
        self._write_status(
            "slice-1",
            state=states_mod.FAILED,
            branch="feat/slice-1",
            worktree=str(ghost),
            last_error="oops",
        )
        rc, out, err = _run("recover", str(self._sprint), "slice-1")
        self.assertEqual(rc, 0, msg=err)
        # Fields still present.
        self.assertIn(str(ghost), out)
        self.assertIn("feat/slice-1", out)
        # No real git invocation; printed the sentinel.
        self.assertIn("worktree directory no longer exists", out)


class RecoverErrorTests(_SprintRootMixin, unittest.TestCase):
    def test_missing_slice_id_exits_1(self) -> None:
        rc, _, err = _run("recover", str(self._sprint), "slice-99")
        self.assertEqual(rc, 1)
        self.assertIn("no status file", err)
        self.assertIn("slice-99", err)

    def test_missing_sprint_dir_exits_1(self) -> None:
        rc, _, err = _run("recover", str(self._tmp / "nope"), "slice-1")
        self.assertEqual(rc, 1)
        self.assertIn("not found", err)


if __name__ == "__main__":
    unittest.main()
