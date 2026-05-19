"""Tests for naml.gitops.merge_pr_squash race-condition retry."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from naml import gitops
from naml.gitops import GhError, merge_pr_squash


_BASE_MODIFIED_STDERR = (
    "gh pr merge naml/sprint-x/slice-1 --repo o/r --squash --delete-branch "
    "exited 1\nGraphQL: Base branch was modified. Review and try the merge "
    "again. (mergePullRequest)"
)


class MergePrSquashRetryTests(unittest.TestCase):
    def test_clean_merge_runs_once(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, *, cwd):  # noqa: ARG001
            calls.append(list(cmd))
            return ""

        with patch.object(gitops, "_run", side_effect=fake_run), \
             patch.object(gitops, "time") as fake_time:
            merge_pr_squash("naml/foo/bar", cwd=Path("/tmp/wt"), repo="o/r")
            self.assertEqual(fake_time.sleep.call_count, 0)

        self.assertEqual(len(calls), 1)

    def test_base_modified_triggers_sleep_then_retry(self) -> None:
        attempts = {"n": 0}

        def fake_run(cmd, *, cwd):  # noqa: ARG001
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise GhError(_BASE_MODIFIED_STDERR)
            return ""

        with patch.object(gitops, "_run", side_effect=fake_run), \
             patch.object(gitops, "time") as fake_time:
            merge_pr_squash("naml/foo/bar", cwd=Path("/tmp/wt"), repo="o/r")
            # Slept once before the retry.
            self.assertEqual(fake_time.sleep.call_count, 1)
            # Slept the configured duration.
            slept_for = fake_time.sleep.call_args[0][0]
            self.assertGreaterEqual(slept_for, 1)

        self.assertEqual(attempts["n"], 2)

    def test_non_race_error_does_not_retry(self) -> None:
        attempts = {"n": 0}

        def fake_run(cmd, *, cwd):  # noqa: ARG001
            attempts["n"] += 1
            raise GhError("gh pr merge exited 1\nremote: permission denied")

        with patch.object(gitops, "_run", side_effect=fake_run), \
             patch.object(gitops, "time") as fake_time:
            with self.assertRaises(GhError):
                merge_pr_squash("naml/foo/bar", cwd=Path("/tmp/wt"), repo="o/r")
            self.assertEqual(fake_time.sleep.call_count, 0)

        self.assertEqual(attempts["n"], 1)

    def test_double_base_modified_raises(self) -> None:
        def fake_run(cmd, *, cwd):  # noqa: ARG001
            raise GhError(_BASE_MODIFIED_STDERR)

        with patch.object(gitops, "_run", side_effect=fake_run), \
             patch.object(gitops, "time"):
            with self.assertRaises(GhError) as ctx:
                merge_pr_squash("naml/foo/bar", cwd=Path("/tmp/wt"), repo="o/r")
            self.assertIn("base branch was modified", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
