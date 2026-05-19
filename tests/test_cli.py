"""Tests for naml.cli — migrate-config, show-config, inspect-sprint."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from naml.cli import main
from naml.config import LEGACY_CONFIG_FILENAME, NEW_CONFIG_RELPATH

from tests.test_package import _build_sprint


FIXTURES = Path(__file__).parent / "fixtures"


class _TempMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-cli-")).resolve()

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class MigrateConfigTests(_TempMixin, unittest.TestCase):
    def test_migrates_legacy_to_naml(self) -> None:
        legacy = self._tmp / LEGACY_CONFIG_FILENAME
        legacy.write_text((FIXTURES / "legacy_config.toml").read_text())
        rc, out, err = _run("migrate-config", "--root", str(self._tmp))
        self.assertEqual(rc, 0, msg=err)
        new_path = self._tmp / NEW_CONFIG_RELPATH
        self.assertTrue(new_path.is_file())
        # Same content; strict rename.
        self.assertEqual(new_path.read_text(), legacy.read_text())

    def test_dry_run_does_not_write(self) -> None:
        legacy = self._tmp / LEGACY_CONFIG_FILENAME
        legacy.write_text((FIXTURES / "legacy_config.toml").read_text())
        rc, out, _ = _run("migrate-config", "--root", str(self._tmp), "--dry-run")
        self.assertEqual(rc, 0)
        self.assertFalse((self._tmp / NEW_CONFIG_RELPATH).is_file())
        self.assertIn("would copy", out)

    def test_refuses_to_overwrite_without_force(self) -> None:
        (self._tmp / LEGACY_CONFIG_FILENAME).write_text(
            (FIXTURES / "legacy_config.toml").read_text()
        )
        new_path = self._tmp / NEW_CONFIG_RELPATH
        new_path.parent.mkdir(parents=True)
        new_path.write_text("# pre-existing\n")
        rc, _, err = _run("migrate-config", "--root", str(self._tmp))
        self.assertEqual(rc, 1)
        self.assertIn("already exists", err)
        # File untouched.
        self.assertEqual(new_path.read_text(), "# pre-existing\n")

    def test_force_overwrites(self) -> None:
        legacy = self._tmp / LEGACY_CONFIG_FILENAME
        legacy.write_text((FIXTURES / "legacy_config.toml").read_text())
        new_path = self._tmp / NEW_CONFIG_RELPATH
        new_path.parent.mkdir(parents=True)
        new_path.write_text("# stale\n")
        rc, _, _ = _run("migrate-config", "--root", str(self._tmp), "--force")
        self.assertEqual(rc, 0)
        self.assertEqual(new_path.read_text(), legacy.read_text())

    def test_errors_when_no_legacy_file(self) -> None:
        rc, _, err = _run("migrate-config", "--root", str(self._tmp))
        self.assertEqual(rc, 1)
        self.assertIn("no .agents-orchestrator.toml", err)


class ShowConfigTests(_TempMixin, unittest.TestCase):
    def test_emits_json_summary(self) -> None:
        target = self._tmp / NEW_CONFIG_RELPATH
        target.parent.mkdir(parents=True)
        target.write_text((FIXTURES / "naml_config.toml").read_text())
        rc, out, _ = _run("show-config", "--root", str(self._tmp))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["repo"], "owner/example-v2")
        self.assertEqual(payload["source_format"], "naml")
        self.assertEqual(payload["parallel_lanes_default"], 4)

    def test_errors_when_no_config_found(self) -> None:
        rc, _, err = _run("show-config", "--root", str(self._tmp))
        self.assertEqual(rc, 1)
        self.assertIn("no naml config", err)


class InspectSprintTests(_TempMixin, unittest.TestCase):
    def test_inspects_valid_sprint(self) -> None:
        path = _build_sprint(self._tmp)
        rc, out, _ = _run("inspect-sprint", str(path))
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], "2026-05-19-test-sprint")
        self.assertEqual(payload["dag_width"], 1)  # linear 1→2
        self.assertEqual(payload["topological_order"], ["slice-1", "slice-2"])

    def test_returns_error_on_bad_sprint(self) -> None:
        bad = self._tmp / "not-a-sprint"
        bad.mkdir()
        rc, _, err = _run("inspect-sprint", str(bad))
        self.assertEqual(rc, 1)
        self.assertIn("manifest", err)


if __name__ == "__main__":
    unittest.main()
