"""Tests for the safe pause/resume mechanism in ``naml.run`` + supporting
plumbing in ``naml.claude``, ``naml.scheduler``, ``naml.project_state``.

Covers four pieces:

1. ``Scheduler.shutdown()`` unblocks ``pop_ready(blocking=True)`` waiters
   and prevents any subsequent ready slice from being claimed.
2. ``claude._register_active_lane`` / ``terminate_all_active_lanes``
   registry behaviour using fake ``Popen`` doubles.
3. ``project_state.on_sprint_paused`` writes the right transition; a
   subsequent ``on_sprint_start`` for the same sprint records "resumed
   from pause" rather than "started".
4. ``run._handle_pause_signal`` (single-fire drain, second-fire hard kill)
   when invoked directly with a fake scheduler.
"""

from __future__ import annotations

import shutil
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from naml import claude as claude_mod
from naml import project_state as ps
from naml import run as run_mod
from naml.scheduler import Scheduler

from tests.test_package import _build_sprint
from naml.package import load_sprint


class _FakeProc:
    """Stand-in for ``subprocess.Popen`` for registry tests.

    Provides only the surface ``terminate_all_active_lanes`` touches:
    ``.pid`` for ``_kill_group``'s ``os.getpgid`` call. We patch
    ``_kill_group`` directly in the tests that exercise termination so
    no real signals fly.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid


class SchedulerShutdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-pause-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _two_slice_sprint(self):
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": []},
            {"id": "slice-2", "title": "B", "type": "AFK", "depends_on": ["slice-1"], "touches": []},
        ]
        return load_sprint(_build_sprint(self._tmp, slices=slices))

    def test_shutdown_unblocks_waiting_pop_ready(self) -> None:
        sprint = self._two_slice_sprint()
        sched = Scheduler(sprint)
        # Claim slice-1 so slice-2 is pending behind it. A second worker
        # calling pop_ready(blocking=True) will block waiting on slice-2
        # which can never become ready until slice-1 is marked done.
        self.assertEqual(sched.pop_ready(), "slice-1")

        result: list[str | None] = []

        def waiter() -> None:
            result.append(sched.pop_ready(blocking=True))

        t = threading.Thread(target=waiter)
        t.start()
        # Give the waiter time to actually block on the condvar.
        time.sleep(0.05)
        self.assertTrue(t.is_alive(), "waiter should be blocked before shutdown")

        sched.shutdown()
        t.join(timeout=2)
        self.assertFalse(t.is_alive(), "waiter should wake after shutdown")
        self.assertEqual(result, [None])

    def test_shutdown_prevents_subsequent_pops(self) -> None:
        sprint = self._two_slice_sprint()
        sched = Scheduler(sprint)
        sched.shutdown()
        # Even though slice-1 is "ready", a shutdown scheduler must not
        # hand it out — that's how drain mode keeps lanes from picking up
        # new work after Ctrl-C.
        self.assertIsNone(sched.pop_ready(blocking=False))
        self.assertIsNone(sched.pop_ready(blocking=True))
        self.assertTrue(sched.is_shutdown())

    def test_shutdown_is_idempotent(self) -> None:
        sprint = self._two_slice_sprint()
        sched = Scheduler(sprint)
        sched.shutdown()
        sched.shutdown()  # second call is a no-op, must not raise
        self.assertTrue(sched.is_shutdown())


class ClaudeActiveLaneRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Each test gets a clean registry. Modules are singletons in the
        # test process so we restore at teardown.
        self._save = set(claude_mod._active_procs)
        claude_mod._active_procs.clear()

    def tearDown(self) -> None:
        claude_mod._active_procs.clear()
        claude_mod._active_procs.update(self._save)

    def test_register_and_unregister_tracks_membership(self) -> None:
        p = _FakeProc(pid=1234)
        claude_mod._register_active_lane(p)
        self.assertIn(p, claude_mod._active_procs)
        claude_mod._unregister_active_lane(p)
        self.assertNotIn(p, claude_mod._active_procs)

    def test_unregister_unknown_is_safe(self) -> None:
        # Calling unregister on a proc never registered must not raise.
        claude_mod._unregister_active_lane(_FakeProc(pid=99))

    def test_terminate_all_signals_each_registered_proc(self) -> None:
        procs = [_FakeProc(pid=10), _FakeProc(pid=11), _FakeProc(pid=12)]
        for p in procs:
            claude_mod._register_active_lane(p)

        with mock.patch.object(claude_mod, "_kill_group") as killer:
            n = claude_mod.terminate_all_active_lanes()

        self.assertEqual(n, 3)
        self.assertEqual(killer.call_count, 3)
        killed_procs = {call.args[0] for call in killer.call_args_list}
        self.assertEqual(killed_procs, set(procs))

    def test_terminate_all_with_empty_registry(self) -> None:
        with mock.patch.object(claude_mod, "_kill_group") as killer:
            n = claude_mod.terminate_all_active_lanes()
        self.assertEqual(n, 0)
        killer.assert_not_called()


class ProjectStatePausedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-pause-state-")).resolve()
        self.naml = self._tmp / ".naml"

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_on_sprint_paused_writes_paused_state(self) -> None:
        ps.on_sprint_start(self.naml, "s1")  # active

        state = ps.on_sprint_paused(self.naml, "s1", mode="drain")
        self.assertEqual(state.state, ps.PAUSED)
        last = state.transitions[-1]
        self.assertEqual(last.state, ps.PAUSED)
        self.assertIn("drain", last.detail)

    def test_on_sprint_paused_forced_mode_records_forced(self) -> None:
        ps.on_sprint_start(self.naml, "s1")
        state = ps.on_sprint_paused(self.naml, "s1", mode="forced")
        self.assertIn("forced", state.transitions[-1].detail)

    def test_on_sprint_paused_is_idempotent(self) -> None:
        ps.on_sprint_start(self.naml, "s1")
        state1 = ps.on_sprint_paused(self.naml, "s1")
        n1 = len(state1.transitions)
        state2 = ps.on_sprint_paused(self.naml, "s1")
        # Already paused; no new transition row should be written.
        self.assertEqual(len(state2.transitions), n1)
        self.assertEqual(state2.state, ps.PAUSED)

    def test_on_sprint_start_after_pause_records_resume(self) -> None:
        ps.on_sprint_start(self.naml, "s1")
        ps.on_sprint_paused(self.naml, "s1")

        state = ps.on_sprint_start(self.naml, "s1")
        self.assertEqual(state.state, ps.ACTIVE)
        last = state.transitions[-1]
        self.assertEqual(last.state, ps.ACTIVE)
        self.assertIn("resumed from pause", last.detail)

    def test_resume_does_not_double_count_sprints_started(self) -> None:
        ps.on_sprint_start(self.naml, "s1")
        ps.on_sprint_paused(self.naml, "s1")
        state = ps.on_sprint_start(self.naml, "s1")
        # Resume must NOT bump the sprints_started counter — that was
        # paid when the sprint first began.
        self.assertEqual(state.metrics.sprints_started, 1)


class _StubScheduler:
    """Minimal Scheduler stand-in for the signal-handler tests. Captures
    whether ``shutdown()`` was called and pretends ``is_shutdown()`` flips
    accordingly so the handler's idempotency check works.
    """

    def __init__(self) -> None:
        self.shutdown_calls = 0
        self._is_shutdown = False

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._is_shutdown = True

    def is_shutdown(self) -> bool:
        return self._is_shutdown


class PauseSignalHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        run_mod._reset_pause_state()
        self._prev_sched = run_mod._current_scheduler
        self.sched = _StubScheduler()
        run_mod._current_scheduler = self.sched

    def tearDown(self) -> None:
        run_mod._reset_pause_state()
        run_mod._current_scheduler = self._prev_sched

    def test_first_sigint_drains_via_scheduler_shutdown(self) -> None:
        with mock.patch.object(claude_mod, "terminate_all_active_lanes") as killer:
            run_mod._handle_pause_signal(signal.SIGINT, None)

        self.assertTrue(run_mod._pause_requested.is_set())
        self.assertFalse(run_mod._kill_requested.is_set())
        self.assertEqual(self.sched.shutdown_calls, 1)
        killer.assert_not_called()

    def test_second_sigint_escalates_to_hard_kill(self) -> None:
        with mock.patch.object(
            claude_mod, "terminate_all_active_lanes", return_value=2
        ) as killer:
            run_mod._handle_pause_signal(signal.SIGINT, None)  # drain
            run_mod._handle_pause_signal(signal.SIGINT, None)  # hard kill

        self.assertTrue(run_mod._pause_requested.is_set())
        self.assertTrue(run_mod._kill_requested.is_set())
        self.assertEqual(killer.call_count, 1)

    def test_sigterm_skips_drain_and_kills_immediately(self) -> None:
        with mock.patch.object(
            claude_mod, "terminate_all_active_lanes", return_value=1
        ) as killer:
            run_mod._handle_pause_signal(signal.SIGTERM, None)

        # SIGTERM is interpreted as "stop now" — drain flag may or may
        # not be set; what matters is the hard-kill side effects.
        self.assertTrue(run_mod._kill_requested.is_set())
        killer.assert_called_once()
        # And the scheduler is also stopped so no new work is handed out.
        self.assertEqual(self.sched.shutdown_calls, 1)

    def test_hard_kill_is_idempotent(self) -> None:
        with mock.patch.object(
            claude_mod, "terminate_all_active_lanes", return_value=0
        ) as killer:
            run_mod._handle_pause_signal(signal.SIGINT, None)
            run_mod._handle_pause_signal(signal.SIGINT, None)
            run_mod._handle_pause_signal(signal.SIGINT, None)
            run_mod._handle_pause_signal(signal.SIGINT, None)

        # Only one hard-kill invocation despite many escalation attempts.
        self.assertEqual(killer.call_count, 1)


class SignalHandlerInstallTests(unittest.TestCase):
    """Smoke test: ``_install_signal_handlers`` returns previous handlers on
    the main thread and ``None`` off it. Restoring is a no-op for ``None``.
    """

    def test_install_off_main_thread_returns_none(self) -> None:
        result: list[object] = []

        def worker() -> None:
            result.append(run_mod._install_signal_handlers())

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertEqual(result, [None])

    def test_restore_with_none_is_a_noop(self) -> None:
        # Just must not raise.
        run_mod._restore_signal_handlers(None)


if __name__ == "__main__":
    unittest.main()
