"""Tests for the [worktree] symlinks config + gitops worktree_add symlink wiring."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from naml.config import (
    DEFAULT_WORKTREE_SYMLINKS,
    LEGACY_CONFIG_FILENAME,
    NEW_CONFIG_RELPATH,
    ConfigError,
    load_config,
)
from naml import gitops


class _TempMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-worktree-")).resolve()

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)


class WorktreeSymlinkConfigTests(_TempMixin, unittest.TestCase):
    """Ensure [worktree] symlinks parses correctly."""

    def _write(self, body: str) -> None:
        target = self._tmp / NEW_CONFIG_RELPATH
        target.parent.mkdir(parents=True)
        target.write_text(textwrap.dedent(body))

    def test_default_when_section_missing(self) -> None:
        self._write(
            """
            [repo]
            slug = "owner/repo"

            [[gates]]
            name = "lint"
            argv = ["pnpm", "lint"]
            """
        )
        cfg = load_config(self._tmp)
        self.assertEqual(cfg.worktree_symlinks, DEFAULT_WORKTREE_SYMLINKS)

    def test_explicit_empty_list_overrides_default(self) -> None:
        self._write(
            """
            [repo]
            slug = "owner/repo"

            [[gates]]
            name = "lint"
            argv = ["pnpm", "lint"]

            [worktree]
            symlinks = []
            """
        )
        cfg = load_config(self._tmp)
        self.assertEqual(cfg.worktree_symlinks, ())

    def test_explicit_list_kept(self) -> None:
        self._write(
            """
            [repo]
            slug = "owner/repo"

            [[gates]]
            name = "lint"
            argv = ["pnpm", "lint"]

            [worktree]
            symlinks = [".venv", "node_modules", ".env"]
            """
        )
        cfg = load_config(self._tmp)
        self.assertEqual(cfg.worktree_symlinks, (".venv", "node_modules", ".env"))

    def test_whitespace_entries_dropped(self) -> None:
        self._write(
            """
            [repo]
            slug = "owner/repo"

            [[gates]]
            name = "lint"
            argv = ["pnpm", "lint"]

            [worktree]
            symlinks = [" .venv ", "", "node_modules"]
            """
        )
        cfg = load_config(self._tmp)
        self.assertEqual(cfg.worktree_symlinks, (".venv", "node_modules"))

    def test_non_list_rejected(self) -> None:
        self._write(
            """
            [repo]
            slug = "owner/repo"

            [[gates]]
            name = "lint"
            argv = ["pnpm", "lint"]

            [worktree]
            symlinks = ".venv"
            """
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self._tmp)
        self.assertIn("symlinks", str(ctx.exception))


class GitopsWorktreeSymlinkTests(_TempMixin, unittest.TestCase):
    """worktree_add must symlink configured entries into the new worktree."""

    def _init_repo(self) -> Path:
        repo = self._tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("hi\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        # Stand up a fake "origin" pointing at a bare clone so origin/main resolves.
        bare = self._tmp / "origin.git"
        subprocess.run(
            ["git", "clone", "--bare", "-q", str(repo), str(bare)], check=True
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(bare)], cwd=repo, check=True
        )
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=repo, check=True)
        # Create a .venv directory + a .env file to symlink.
        (repo / ".venv" / "bin").mkdir(parents=True)
        (repo / ".venv" / "bin" / "ruff").write_text("#!/bin/sh\necho 'fake ruff'\n")
        os.chmod(repo / ".venv" / "bin" / "ruff", 0o755)
        (repo / ".env").write_text("SECRET=stub\n")
        return repo

    def test_symlinks_created_in_worktree(self) -> None:
        repo = self._init_repo()
        worktree = self._tmp / "wt"
        gitops.worktree_add(
            worktree,
            repo_root=repo,
            base_branch="main",
            symlinks=(".venv", ".env"),
        )
        link_venv = worktree / ".venv"
        link_env = worktree / ".env"
        self.assertTrue(link_venv.is_symlink())
        self.assertTrue(link_env.is_symlink())
        self.assertEqual(link_venv.resolve(), (repo / ".venv").resolve())
        self.assertEqual(link_env.resolve(), (repo / ".env").resolve())
        # The agent inside the worktree can run the symlinked binary.
        ruff_path = worktree / ".venv" / "bin" / "ruff"
        self.assertTrue(ruff_path.exists())

    def test_missing_source_silently_skipped(self) -> None:
        repo = self._init_repo()
        worktree = self._tmp / "wt"
        # node_modules doesn't exist in the repo; the call must not raise.
        gitops.worktree_add(
            worktree,
            repo_root=repo,
            base_branch="main",
            symlinks=(".venv", "node_modules"),
        )
        self.assertTrue((worktree / ".venv").is_symlink())
        self.assertFalse((worktree / "node_modules").exists())

    def test_empty_symlinks_arg_no_op(self) -> None:
        repo = self._init_repo()
        worktree = self._tmp / "wt"
        gitops.worktree_add(
            worktree, repo_root=repo, base_branch="main", symlinks=()
        )
        self.assertFalse((worktree / ".venv").exists())

    def test_does_not_clobber_tracked_path(self) -> None:
        """If git itself put a file at the symlink target, don't replace it."""
        repo = self._init_repo()
        # Track a file with the SAME name as a symlink target so the worktree
        # will materialise it from the index. (`.venv` would conflict with our
        # gitignored-tool convention, so use a path that's plausibly tracked.)
        (repo / "config.yaml").write_text("real: tracked\n")
        subprocess.run(["git", "add", "config.yaml"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "track config"], cwd=repo, check=True
        )
        # Push so the bare origin has it, so origin/main has the file.
        subprocess.run(
            ["git", "push", "-q", "origin", "main"], cwd=repo, check=True
        )
        # Make a copy in the source repo at a DIFFERENT real location so the
        # symlink would have something to point at.
        decoy = self._tmp / "decoy.yaml"
        decoy.write_text("decoy: yes\n")

        worktree = self._tmp / "wt"
        gitops.worktree_add(
            worktree, repo_root=repo, base_branch="main", symlinks=("config.yaml",)
        )
        path = worktree / "config.yaml"
        self.assertFalse(path.is_symlink())
        # The tracked file wins.
        self.assertIn("real:", path.read_text())


if __name__ == "__main__":
    unittest.main()
