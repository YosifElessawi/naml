"""Tests for the gate-runnability preflight in ``naml.run``.

The preflight catches the most common "wasted hour" failure mode: the
slice agent does its work, the test gate fires, the configured executable
(e.g. ``python``) doesn't exist on this machine, and the gate fails after
the agent has already burned a session.
"""

from __future__ import annotations

import logging
import unittest

from naml.config import Gate
from naml.run import GatePreflightError, preflight_gates


class GatePreflightTests(unittest.TestCase):
    def test_basename_on_path_passes(self) -> None:
        # ``python3`` is on PATH everywhere naml ships.
        preflight_gates([Gate(name="test", argv=["python3", "-m", "pytest"])])

    def test_basename_missing_from_path_raises(self) -> None:
        with self.assertRaises(GatePreflightError) as ctx:
            preflight_gates(
                [Gate(name="test", argv=["this-binary-does-not-exist-xyz", "-q"])]
            )
        msg = str(ctx.exception)
        self.assertIn("test", msg)
        self.assertIn("this-binary-does-not-exist-xyz", msg)

    def test_absolute_path_existing_executable_passes(self) -> None:
        # /usr/bin/true is part of POSIX; available on Linux + macOS.
        preflight_gates([Gate(name="noop", argv=["/usr/bin/true"])])

    def test_absolute_path_missing_raises(self) -> None:
        with self.assertRaises(GatePreflightError) as ctx:
            preflight_gates([Gate(name="lint", argv=["/nope/nope/nope"])])
        self.assertIn("/nope/nope/nope", str(ctx.exception))

    def test_relative_path_warns_does_not_raise(self) -> None:
        # Relative paths can resolve inside a slice worktree (e.g.
        # ``./.venv/bin/python``); the orchestrator can't check from here.
        with self.assertLogs("naml.run", level="WARNING") as cm:
            preflight_gates(
                [Gate(name="lint", argv=["./relative/thing", "--fix"])]
            )
        self.assertTrue(
            any("relative path" in m for m in cm.output),
            msg=cm.output,
        )

    def test_multiple_broken_gates_all_listed(self) -> None:
        gates = [
            Gate(name="lint", argv=["nonexistent-linter-abc"]),
            Gate(name="test", argv=["python", "-m", "pytest"]),  # 'python' often missing on macOS
            Gate(name="ok",   argv=["python3", "-m", "unittest"]),
            Gate(name="bad-abs", argv=["/var/empty/missing-bin"]),
        ]
        # Run preflight; capture which gates were declared broken.
        try:
            preflight_gates(gates)
        except GatePreflightError as exc:
            msg = str(exc)
        else:
            self.fail("expected GatePreflightError")

        # Every broken gate must appear in the message.
        # Note: 'python' may or may not exist on the test host. We assert
        # presence only for the deterministic-missing ones, and on the
        # working one's absence (it must NOT appear).
        self.assertIn("lint", msg)
        self.assertIn("nonexistent-linter-abc", msg)
        self.assertIn("bad-abs", msg)
        self.assertIn("/var/empty/missing-bin", msg)
        self.assertNotIn("ok:", msg)

    def test_empty_argv_is_treated_as_broken(self) -> None:
        with self.assertRaises(GatePreflightError):
            preflight_gates([Gate(name="empty", argv=[])])


if __name__ == "__main__":
    unittest.main()
