"""Tests for naml.config — dual-format loader + validation.

Runs under stdlib unittest: ``python3 -m unittest discover tests -v``.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from naml.config import (
    DEFAULT_PARALLEL_LANES,
    HARD_LANE_CAP,
    LEGACY_CONFIG_FILENAME,
    NEW_CONFIG_RELPATH,
    ConfigError,
    find_config_file,
    load_config,
)


FIXTURES = Path(__file__).parent / "fixtures"


class _TempRepoMixin:
    """Spin up an isolated repo root for each test."""

    def setUp(self) -> None:  # type: ignore[override]
        # resolve() collapses the macOS /var → /private/var symlink so paths
        # compared in assertions match what find_config_file resolves to.
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-cfg-")).resolve()

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write(self, rel: str, content: str) -> Path:
        path = self._tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path


class FindConfigFileTests(_TempRepoMixin, unittest.TestCase):
    def test_finds_legacy_when_only_legacy_present(self) -> None:
        legacy = self._tmp / LEGACY_CONFIG_FILENAME
        legacy.write_text((FIXTURES / "legacy_config.toml").read_text())
        path, fmt = find_config_file(self._tmp)
        self.assertEqual(path, legacy.resolve())
        self.assertEqual(fmt, "legacy")

    def test_finds_naml_when_only_naml_present(self) -> None:
        target = self._tmp / NEW_CONFIG_RELPATH
        target.parent.mkdir(parents=True)
        target.write_text((FIXTURES / "naml_config.toml").read_text())
        path, fmt = find_config_file(self._tmp)
        self.assertEqual(path, target.resolve())
        self.assertEqual(fmt, "naml")

    def test_prefers_naml_when_both_present(self) -> None:
        (self._tmp / LEGACY_CONFIG_FILENAME).write_text(
            (FIXTURES / "legacy_config.toml").read_text()
        )
        naml_path = self._tmp / NEW_CONFIG_RELPATH
        naml_path.parent.mkdir(parents=True)
        naml_path.write_text((FIXTURES / "naml_config.toml").read_text())
        path, fmt = find_config_file(self._tmp)
        self.assertEqual(fmt, "naml")
        self.assertEqual(path, naml_path.resolve())

    def test_walks_upwards(self) -> None:
        (self._tmp / LEGACY_CONFIG_FILENAME).write_text(
            (FIXTURES / "legacy_config.toml").read_text()
        )
        deep = self._tmp / "a" / "b" / "c"
        deep.mkdir(parents=True)
        path, fmt = find_config_file(deep)
        self.assertEqual(fmt, "legacy")
        self.assertTrue(path.is_file())

    def test_raises_when_nothing_found(self) -> None:
        with self.assertRaises(ConfigError):
            find_config_file(self._tmp)


class LoadLegacyConfigTests(_TempRepoMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        (self._tmp / LEGACY_CONFIG_FILENAME).write_text(
            (FIXTURES / "legacy_config.toml").read_text()
        )

    def test_loads_with_v2_defaults(self) -> None:
        cfg = load_config(self._tmp)
        self.assertEqual(cfg.source_format, "legacy")
        self.assertEqual(cfg.repo, "owner/example")
        self.assertEqual(cfg.base_branch, "main")
        # repo_root for legacy is the dir containing the toml file.
        self.assertEqual(cfg.repo_root, self._tmp)
        self.assertEqual([g.name for g in cfg.gates], ["lint", "test"])
        self.assertEqual(cfg.run_cap_minutes, 25)
        self.assertEqual(cfg.max_retries, 1)
        # V2 defaults applied even when keys are absent.
        self.assertEqual(cfg.parallel_lanes_default, DEFAULT_PARALLEL_LANES)
        self.assertEqual(cfg.parallel_lanes_max, HARD_LANE_CAP)


class LoadNamlConfigTests(_TempRepoMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        target = self._tmp / NEW_CONFIG_RELPATH
        target.parent.mkdir(parents=True)
        target.write_text((FIXTURES / "naml_config.toml").read_text())

    def test_loads_with_v2_extensions(self) -> None:
        cfg = load_config(self._tmp)
        self.assertEqual(cfg.source_format, "naml")
        self.assertEqual(cfg.repo, "owner/example-v2")
        # For .naml/config.toml the repo_root is .naml/'s parent.
        self.assertEqual(cfg.repo_root, self._tmp)
        self.assertEqual(cfg.parallel_lanes_default, 4)
        self.assertEqual(cfg.parallel_lanes_max, 6)
        self.assertEqual(cfg.stop_after, "review")
        self.assertTrue(cfg.auto_review)
        self.assertEqual(
            cfg.sprints_path, self._tmp / ".naml" / "sprints"
        )
        self.assertEqual(
            cfg.feedback_inbox_path,
            self._tmp / "docs" / "feedback" / "inbox.md",
        )


class ConfigValidationTests(_TempRepoMixin, unittest.TestCase):
    def test_rejects_missing_repo_slug(self) -> None:
        self.write(
            LEGACY_CONFIG_FILENAME,
            """
            [repo]
            base_branch = "main"

            [[gates]]
            name = "lint"
            argv = ["pnpm", "lint"]
            """,
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self._tmp)
        self.assertIn("slug", str(ctx.exception))

    def test_rejects_repo_slug_without_slash(self) -> None:
        self.write(
            LEGACY_CONFIG_FILENAME,
            """
            [repo]
            slug = "notaslug"

            [[gates]]
            name = "lint"
            argv = ["pnpm", "lint"]
            """,
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self._tmp)
        self.assertIn("owner/name", str(ctx.exception))

    def test_rejects_zero_gates(self) -> None:
        self.write(
            LEGACY_CONFIG_FILENAME,
            """
            [repo]
            slug = "owner/repo"
            """,
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self._tmp)
        self.assertIn("[[gates]]", str(ctx.exception))

    def test_rejects_bad_gate_argv(self) -> None:
        self.write(
            LEGACY_CONFIG_FILENAME,
            """
            [repo]
            slug = "owner/repo"

            [[gates]]
            name = "lint"
            argv = "not-a-list"
            """,
        )
        with self.assertRaises(ConfigError):
            load_config(self._tmp)

    def test_rejects_unknown_stop_after(self) -> None:
        self.write(
            LEGACY_CONFIG_FILENAME,
            """
            [repo]
            slug = "owner/repo"

            [[gates]]
            name = "lint"
            argv = ["pnpm", "lint"]

            [pipeline]
            stop_after = "elsewhere"
            """,
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self._tmp)
        self.assertIn("stop_after", str(ctx.exception))

    def test_rejects_lanes_max_below_default(self) -> None:
        (self._tmp / ".naml").mkdir()
        (self._tmp / NEW_CONFIG_RELPATH).write_text(
            textwrap.dedent(
                """
                [repo]
                slug = "owner/repo"

                [[gates]]
                name = "lint"
                argv = ["pnpm", "lint"]

                [lanes]
                default = 6
                max = 3
                """
            )
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self._tmp)
        self.assertIn("max", str(ctx.exception))

    def test_rejects_malformed_toml(self) -> None:
        self.write(LEGACY_CONFIG_FILENAME, "this = is = not = toml\n")
        with self.assertRaises(ConfigError):
            load_config(self._tmp)


if __name__ == "__main__":
    unittest.main()
