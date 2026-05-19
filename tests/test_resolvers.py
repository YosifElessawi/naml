"""Tests for naml.resolvers — scripted Tier-2 conflict handlers."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from naml import resolvers
from naml.resolvers import (
    has_conflict_markers,
    resolve_gitignore_union,
    resolve_lockfile,
    split_conflict_blocks,
    try_resolve,
)


class _GitTempMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-resolv-")).resolve()
        self.repo = self._tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)


# --- conflict-marker primitives -----------------------------------------


class ConflictMarkerTests(unittest.TestCase):
    def test_has_markers_finds_them(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            p = tmp / "f.txt"
            p.write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n")
            self.assertTrue(has_conflict_markers(p))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_has_markers_no_markers(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            p = tmp / "f.txt"
            p.write_text("just\nnormal\ncontent\n")
            self.assertFalse(has_conflict_markers(p))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_split_blocks_picks_ours_and_theirs(self) -> None:
        text = textwrap.dedent(
            """\
            line one
            <<<<<<< HEAD
            alpha
            beta
            =======
            gamma
            delta
            >>>>>>> branch-x
            line tail
            """
        )
        blocks = split_conflict_blocks(text)
        self.assertEqual(len(blocks), 1)
        ours, theirs, label = blocks[0]
        self.assertEqual(ours, "alpha\nbeta")
        self.assertEqual(theirs, "gamma\ndelta")
        self.assertEqual(label, "branch-x")

    def test_split_returns_empty_when_no_conflicts(self) -> None:
        self.assertEqual(split_conflict_blocks("no markers here\n"), [])


# --- gitignore union ----------------------------------------------------


class GitignoreUnionTests(_GitTempMixin, unittest.TestCase):
    def _write(self, name: str, body: str) -> Path:
        p = self.repo / name
        p.write_text(textwrap.dedent(body))
        subprocess.run(["git", "add", name], cwd=self.repo, check=True)
        return p

    def test_unions_two_sides(self) -> None:
        self._write(
            ".gitignore",
            """\
            *.pyc
            <<<<<<< HEAD
            .venv/
            .idea/
            =======
            .venv/
            .vscode/
            >>>>>>> theirs
            build/
            """,
        )
        outcome = resolve_gitignore_union(".gitignore", worktree=self.repo)
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.resolved)
        result = (self.repo / ".gitignore").read_text()
        # Conflict markers gone.
        self.assertNotIn("<<<<<<<", result)
        # All three union entries present.
        self.assertIn(".venv/", result)
        self.assertIn(".idea/", result)
        self.assertIn(".vscode/", result)
        # Dedup: .venv/ only once.
        self.assertEqual(result.count(".venv/"), 1)
        # File staged.
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=self.repo, capture_output=True, text=True,
        )
        self.assertIn(".gitignore", proc.stdout)

    def test_not_applicable_when_filename_wrong(self) -> None:
        p = self.repo / "config.yaml"
        p.write_text("<<<<<<<\nfoo\n=======\nbar\n>>>>>>>\n")
        outcome = resolve_gitignore_union("config.yaml", worktree=self.repo)
        self.assertFalse(outcome.applied)

    def test_not_applicable_when_no_conflict_markers(self) -> None:
        self._write(".gitignore", "*.pyc\n")
        outcome = resolve_gitignore_union(".gitignore", worktree=self.repo)
        self.assertFalse(outcome.applied)


# --- lockfile regen -----------------------------------------------------


class LockfileRegenTests(_GitTempMixin, unittest.TestCase):
    def test_not_applicable_for_unrelated_file(self) -> None:
        outcome = resolve_lockfile("src/foo.py", worktree=self.repo)
        self.assertFalse(outcome.applied)

    def test_applies_but_unresolved_when_pm_missing(self) -> None:
        # Write a fake conflicted lockfile.
        p = self.repo / "package-lock.json"
        p.write_text("{}")
        subprocess.run(["git", "add", "package-lock.json"],
                       cwd=self.repo, check=True)

        # Stub out shutil.which → None to simulate npm absent.
        original_which = shutil.which
        shutil.which = lambda _: None
        try:
            outcome = resolve_lockfile("package-lock.json", worktree=self.repo)
        finally:
            shutil.which = original_which

        self.assertTrue(outcome.applied)
        self.assertFalse(outcome.resolved)
        self.assertIn("not on PATH", outcome.detail)


# --- dispatcher + listing ------------------------------------------------


class TryResolveDispatchTests(_GitTempMixin, unittest.TestCase):
    def test_dispatches_to_first_applicable(self) -> None:
        path = self.repo / ".gitignore"
        path.write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> br\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.repo, check=True)
        outcome = try_resolve(".gitignore", worktree=self.repo)
        self.assertTrue(outcome.applied)
        self.assertEqual(outcome.name, "gitignore-union")

    def test_not_applicable_when_no_resolver(self) -> None:
        outcome = try_resolve("src/random.py", worktree=self.repo)
        self.assertFalse(outcome.applied)
        self.assertEqual(outcome.name, "none")


class ListConflictFilesTests(_GitTempMixin, unittest.TestCase):
    def test_empty_when_clean(self) -> None:
        self.assertEqual(resolvers.list_conflict_files(worktree=self.repo), [])

    def test_lists_real_conflict_files(self) -> None:
        # Create a real conflict via git's plumbing: commit on two branches
        # that both touch the same file, then merge.
        (self.repo / "a.txt").write_text("base\n")
        subprocess.run(["git", "add", "a.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=self.repo, check=True)

        # Branch alpha.
        subprocess.run(["git", "checkout", "-q", "-b", "alpha"],
                       cwd=self.repo, check=True)
        (self.repo / "a.txt").write_text("alpha\n")
        subprocess.run(["git", "commit", "-aq", "-m", "alpha"],
                       cwd=self.repo, check=True)

        # Branch beta from main.
        subprocess.run(["git", "checkout", "-q", "main"], cwd=self.repo, check=True)
        subprocess.run(["git", "checkout", "-q", "-b", "beta"],
                       cwd=self.repo, check=True)
        (self.repo / "a.txt").write_text("beta\n")
        subprocess.run(["git", "commit", "-aq", "-m", "beta"],
                       cwd=self.repo, check=True)

        # Merge alpha into beta → conflict.
        result = subprocess.run(
            ["git", "merge", "alpha"], cwd=self.repo, capture_output=True
        )
        self.assertNotEqual(result.returncode, 0, "merge should conflict")
        files = resolvers.list_conflict_files(worktree=self.repo)
        self.assertEqual(files, ["a.txt"])


if __name__ == "__main__":
    unittest.main()
