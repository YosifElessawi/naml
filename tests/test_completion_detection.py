"""Tests for naml.claude completion detection.

The `_scan_for_success_event` function is the heart of the Stage-3 dogfood
fix: it lets the lane worker treat the agent as "logically done" once a
``type:result`` event lands in the log, rather than waiting on
``proc.wait()`` for a subprocess that's blocked on a slow Stop hook.

We also exercise the integration via ``_wait_under_cap`` with a fake
subprocess that emits a success-result line, then hangs. The wait should
return ``completed=True, timed_out=False`` well before the wall-clock cap.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from naml.claude import (
    RESULT_GRACE_SECONDS,
    _scan_for_success_event,
    _wait_under_cap,
)


class _TempMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-claude-")).resolve()

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)


def _emit(log: Path, event: dict) -> None:
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


class ScanForSuccessEventTests(_TempMixin, unittest.TestCase):
    def test_empty_log_returns_false(self) -> None:
        log = self._tmp / "x.log"
        log.touch()
        found, off = _scan_for_success_event(log, 0, 0)
        self.assertFalse(found)
        self.assertEqual(off, 0)

    def test_missing_log_returns_false(self) -> None:
        log = self._tmp / "nope.log"
        found, off = _scan_for_success_event(log, 0, 0)
        self.assertFalse(found)
        self.assertEqual(off, 0)

    def test_success_event_detected(self) -> None:
        log = self._tmp / "x.log"
        _emit(log, {"type": "system", "subtype": "init"})
        _emit(log, {"type": "result", "subtype": "success", "is_error": False})
        found, off = _scan_for_success_event(log, 0, 0)
        self.assertTrue(found)
        self.assertGreater(off, 0)

    def test_error_result_does_not_count(self) -> None:
        log = self._tmp / "x.log"
        _emit(log, {"type": "result", "is_error": True})
        found, _ = _scan_for_success_event(log, 0, 0)
        self.assertFalse(found)

    def test_subtype_optional(self) -> None:
        """Some Claude versions omit subtype on the final result event."""
        log = self._tmp / "x.log"
        _emit(log, {"type": "result", "is_error": False})
        found, _ = _scan_for_success_event(log, 0, 0)
        self.assertTrue(found)

    def test_offset_advances_to_avoid_rescan(self) -> None:
        log = self._tmp / "x.log"
        _emit(log, {"type": "system"})
        _emit(log, {"type": "result", "is_error": False})
        found, off1 = _scan_for_success_event(log, 0, 0)
        self.assertTrue(found)
        size = log.stat().st_size
        self.assertEqual(off1, size)
        # No new bytes → no new detection (still cached True from caller side).
        found2, off2 = _scan_for_success_event(log, 0, off1)
        self.assertFalse(found2)
        self.assertEqual(off2, off1)

    def test_garbage_lines_ignored(self) -> None:
        log = self._tmp / "x.log"
        with log.open("w", encoding="utf-8") as fh:
            fh.write("====\nCLAUDE START (cap 30m)\n===\n")
            fh.write("not json\n")
        _emit(log, {"type": "result", "is_error": False})
        found, _ = _scan_for_success_event(log, 0, 0)
        self.assertTrue(found)


class WaitUnderCapIntegrationTests(_TempMixin, unittest.TestCase):
    """Spawn a real subprocess that emits a success line then hangs; verify
    `_wait_under_cap` returns logical completion well before the cap."""

    def _emit_then_hang_script(self, log_path: Path) -> str:
        """Build a tiny Python script that appends a success-result event to
        ``log_path``, then sleeps forever. Stand-in for a Claude session whose
        Stop hook hangs.
        """
        return (
            "import json, sys, time\n"
            f"log = open({str(log_path)!r}, 'a')\n"
            "log.write(json.dumps({'type':'system','subtype':'init'}) + '\\n')\n"
            "log.flush()\n"
            "log.write(json.dumps({'type':'result','is_error':False,'subtype':'success'}) + '\\n')\n"
            "log.flush()\n"
            "time.sleep(120)\n"
        )

    def test_completion_via_result_then_grace_kill(self) -> None:
        log = self._tmp / "agent.log"
        log.write_text("")
        script = self._emit_then_hang_script(log)
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            t0 = time.time()
            # Patch the grace down to keep the test fast.
            import naml.claude as claude_mod

            original_grace = claude_mod.RESULT_GRACE_SECONDS
            claude_mod.RESULT_GRACE_SECONDS = 2
            try:
                completed, timed_out, exit_code = _wait_under_cap(
                    proc,
                    log_path=log,
                    start_offset=0,
                    cap_minutes=5,  # would be 5min in production
                    label="TEST",
                )
            finally:
                claude_mod.RESULT_GRACE_SECONDS = original_grace
            elapsed = time.time() - t0
            self.assertTrue(completed, "should report logical completion")
            self.assertFalse(timed_out, "should NOT report timeout")
            self.assertEqual(exit_code, 0)
            # We should bail in <= grace + a couple polling intervals.
            self.assertLess(elapsed, 10, f"took too long: {elapsed:.1f}s")
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except ProcessLookupError:
                    pass

    def test_natural_exit_before_grace(self) -> None:
        """If the subprocess exits cleanly, we should not wait the grace at all."""
        log = self._tmp / "agent.log"
        log.write_text("")
        # Script: emit success then exit immediately.
        script = (
            "import json\n"
            f"log = open({str(log)!r}, 'a')\n"
            "log.write(json.dumps({'type':'result','is_error':False}) + '\\n')\n"
            "log.flush()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        t0 = time.time()
        completed, timed_out, exit_code = _wait_under_cap(
            proc,
            log_path=log,
            start_offset=0,
            cap_minutes=5,
            label="TEST",
        )
        elapsed = time.time() - t0
        self.assertTrue(completed)
        self.assertFalse(timed_out)
        self.assertEqual(exit_code, 0)
        # Natural-exit path should be sub-second.
        self.assertLess(elapsed, 2, f"too slow on natural exit: {elapsed:.1f}s")


if __name__ == "__main__":
    unittest.main()
