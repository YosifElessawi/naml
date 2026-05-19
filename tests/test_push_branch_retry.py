"""Tests for naml.gitops.push_branch's stale-info retry."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from naml import gitops
from naml.gitops import GitError, push_branch
from pathlib import Path


_STALE_STDERR = (
    "git push -u --force-with-lease origin naml/sprint-x/slice-1 exited 1\n"
    "To https://github.com/example/repo.git\n"
    " ! [rejected]        naml/sprint-x/slice-1 -> naml/sprint-x/slice-1 (stale info)\n"
    "error: failed to push some refs to 'https://github.com/example/repo.git'"
)


class PushBranchRetryTests(unittest.TestCase):
    def test_clean_push_runs_once(self) -> None:
        calls: list[list[str]] = []

        def fake_git(*args: str, cwd: Path) -> str:
            calls.append(list(args))
            return ""

        with patch.object(gitops, "git", side_effect=fake_git):
            push_branch("naml/foo/bar", cwd=Path("/tmp/wt"))

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "push")
        self.assertIn("--force-with-lease", calls[0])

    def test_stale_info_triggers_fetch_then_retry(self) -> None:
        calls: list[tuple[str, list[str]]] = []
        state = {"push_attempts": 0}

        def fake_git(*args: str, cwd: Path) -> str:
            tag = args[0]
            calls.append((tag, list(args)))
            if tag == "push":
                state["push_attempts"] += 1
                if state["push_attempts"] == 1:
                    raise GitError(_STALE_STDERR)
                return ""
            if tag == "fetch":
                return ""
            return ""

        with patch.object(gitops, "git", side_effect=fake_git):
            push_branch("naml/foo/bar", cwd=Path("/tmp/wt"))

        kinds = [c[0] for c in calls]
        self.assertEqual(kinds, ["push", "fetch", "push"])
        # Fetch must use --prune so deleted remote branches drop locally.
        self.assertIn("--prune", calls[1][1])

    def test_fetch_failure_does_not_block_retry(self) -> None:
        state = {"push_attempts": 0}

        def fake_git(*args: str, cwd: Path) -> str:
            tag = args[0]
            if tag == "push":
                state["push_attempts"] += 1
                if state["push_attempts"] == 1:
                    raise GitError(_STALE_STDERR)
                return ""
            if tag == "fetch":
                raise GitError("fetch failed for unrelated reason")
            return ""

        with patch.object(gitops, "git", side_effect=fake_git):
            push_branch("naml/foo/bar", cwd=Path("/tmp/wt"))

        # Retry still attempted despite fetch failure.
        self.assertEqual(state["push_attempts"], 2)

    def test_non_stale_error_does_not_retry(self) -> None:
        calls: list[str] = []

        def fake_git(*args: str, cwd: Path) -> str:
            calls.append(args[0])
            if args[0] == "push":
                raise GitError("git push exited 1\nremote: permission denied")
            return ""

        with patch.object(gitops, "git", side_effect=fake_git):
            with self.assertRaises(GitError):
                push_branch("naml/foo/bar", cwd=Path("/tmp/wt"))

        # Single push attempt, no fetch on error paths other than stale info.
        self.assertEqual(calls.count("push"), 1)
        self.assertNotIn("fetch", calls)

    def test_double_stale_info_raises(self) -> None:
        def fake_git(*args: str, cwd: Path) -> str:
            if args[0] == "push":
                raise GitError(_STALE_STDERR)
            return ""

        with patch.object(gitops, "git", side_effect=fake_git):
            with self.assertRaises(GitError) as ctx:
                push_branch("naml/foo/bar", cwd=Path("/tmp/wt"))
        self.assertIn("stale info", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
