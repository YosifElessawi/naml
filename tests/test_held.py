"""Tests for the HELD slice state (slice-14 of the cockpit-v2 sprint).

Covers four pieces:

1. The sentinel-file helpers in ``naml.state`` round-trip cleanly.
2. ``lane._await_hold_clearance`` waits for the sentinel to disappear,
   recording the held → work transition pair while it parks.
3. ``scheduler.absorb_existing_statuses`` preserves a held state across a
   naml-run restart (re-creates the sentinel if missing).
4. The intervene endpoints (HOLD / RESUME / OPEN_TERMINAL gating /
   MARK_FAILED / SKIP) drive the lane via the same on-disk artifacts.
"""

from __future__ import annotations

import platform
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from naml import lane as lane_mod
from naml import server as server_mod
from naml import state as state_mod
from naml import states


class HoldSentinelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-held-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_write_then_clear_is_idempotent(self) -> None:
        self.assertFalse(state_mod.is_hold_requested(self._tmp, "slice-1"))
        state_mod.write_hold_requested(self._tmp, "slice-1")
        self.assertTrue(state_mod.is_hold_requested(self._tmp, "slice-1"))
        # Idempotent — second write is a no-op, not an error.
        state_mod.write_hold_requested(self._tmp, "slice-1")
        self.assertTrue(state_mod.is_hold_requested(self._tmp, "slice-1"))
        state_mod.clear_hold_requested(self._tmp, "slice-1")
        self.assertFalse(state_mod.is_hold_requested(self._tmp, "slice-1"))
        # Clearing an already-absent sentinel must not raise.
        state_mod.clear_hold_requested(self._tmp, "slice-1")

    def test_sentinel_zero_bytes(self) -> None:
        state_mod.write_hold_requested(self._tmp, "slice-1")
        path = state_mod.hold_requested_path(self._tmp, "slice-1")
        self.assertEqual(path.read_bytes(), b"")


class AwaitHoldClearanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-held-await-")).resolve()
        state_mod.ensure_state_dir(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_no_sentinel_is_a_noop(self) -> None:
        status = state_mod.SliceStatus(slice_id="slice-1", state=states.WORK)
        lane_mod._await_hold_clearance(status, self._tmp, poll_interval=0.01)
        # State and transitions unchanged.
        self.assertEqual(status.state, states.WORK)
        self.assertEqual(status.transitions, [])

    def test_no_duplicate_held_transition_when_already_held(self) -> None:
        # Simulate cross-restart: the slice was already held on disk from
        # a previous naml run; the lane reclaims it and enters
        # ``_await_hold_clearance`` immediately. We should NOT record a
        # second held→held transition (that would clutter the timeline).
        status = state_mod.SliceStatus(slice_id="slice-2", state=states.HELD)
        status.record_transition(state=states.HELD, detail="from prior run")
        state_mod.write_hold_requested(self._tmp, "slice-2")

        def clear_after_delay() -> None:
            time.sleep(0.05)
            state_mod.clear_hold_requested(self._tmp, "slice-2")

        threading.Thread(target=clear_after_delay, daemon=True).start()
        lane_mod._await_hold_clearance(status, self._tmp, poll_interval=0.01)

        # Only one held entry + one work entry — no duplicate held.
        held_count = sum(1 for t in status.transitions if t.state == states.HELD)
        work_count = sum(1 for t in status.transitions if t.state == states.WORK)
        self.assertEqual(held_count, 1, "should not record a second held transition")
        self.assertEqual(work_count, 1)
        self.assertEqual(status.state, states.WORK)

    def test_held_transitions_recorded_and_loop_exits_on_clear(self) -> None:
        status = state_mod.SliceStatus(slice_id="slice-2", state=states.WORK)
        state_mod.write_hold_requested(self._tmp, "slice-2")

        def clear_after_delay() -> None:
            time.sleep(0.05)
            state_mod.clear_hold_requested(self._tmp, "slice-2")

        threading.Thread(target=clear_after_delay, daemon=True).start()
        lane_mod._await_hold_clearance(status, self._tmp, poll_interval=0.01)

        # Two new transitions: → held, then → work.
        states_seen = [t.state for t in status.transitions]
        self.assertEqual(states_seen, [states.HELD, states.WORK])
        self.assertEqual(status.state, states.WORK)
        # State was persisted both times — the cockpit must see "held"
        # while the lane is parked, not just at the end.
        on_disk = state_mod.load_slice_status(self._tmp, "slice-2")
        self.assertIsNotNone(on_disk)
        assert on_disk is not None
        self.assertEqual(on_disk.state, states.WORK)


class HeldStateMachineMembershipTests(unittest.TestCase):
    def test_held_not_in_done_or_failed_partitions(self) -> None:
        self.assertNotIn(states.HELD, states.LANE_DONE_STATES)
        self.assertNotIn(states.HELD, states.LANE_FAILED_STATES)

    def test_held_in_terminal_unlocked_states(self) -> None:
        # The whole point of held — open-terminal becomes legal.
        self.assertIn(states.HELD, states.TERMINAL_UNLOCKED_STATES)

    def test_work_and_pr_are_locked(self) -> None:
        # The other side of the contract: open-terminal must stay locked
        # while naml owns the session.
        self.assertNotIn(states.WORK, states.TERMINAL_UNLOCKED_STATES)
        self.assertNotIn(states.PR, states.TERMINAL_UNLOCKED_STATES)
        self.assertNotIn(states.SETUP, states.TERMINAL_UNLOCKED_STATES)


class SchedulerPreservesHeldTests(unittest.TestCase):
    """If naml crashes during HELD, the next ``naml run`` must not lose it."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-held-resume-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_held_status_left_alone_by_absorb_existing_statuses(self) -> None:
        from naml.package import load_sprint
        from naml.scheduler import Scheduler

        from tests.test_package import _build_sprint

        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": []},
        ]
        sprint_root = _build_sprint(self._tmp, slices=slices)
        sprint = load_sprint(sprint_root)

        # Simulate a held slice with sentinel still on disk.
        state_mod.ensure_state_dir(sprint_root)
        held_status = state_mod.SliceStatus(
            slice_id="slice-1",
            state=states.HELD,
            session_id="sess-real",
            branch="naml/sprint-A/slice-1",
            worktree=str(self._tmp / "wt"),
        )
        held_status.record_transition(state=states.HELD, detail="user HOLD")
        state_mod.save_slice_status(sprint_root, held_status)
        state_mod.write_hold_requested(sprint_root, "slice-1")

        sched = Scheduler(sprint)
        sched.absorb_existing_statuses(sprint_root)

        # State on disk unchanged.
        reloaded = state_mod.load_slice_status(sprint_root, "slice-1")
        assert reloaded is not None
        self.assertEqual(reloaded.state, states.HELD)
        # session_id preserved — RESUME inside the same naml run will reuse it.
        self.assertEqual(reloaded.session_id, "sess-real")
        # Sentinel still there (and re-written if absent).
        self.assertTrue(state_mod.is_hold_requested(sprint_root, "slice-1"))

    def test_absorb_recreates_sentinel_if_lost(self) -> None:
        from naml.package import load_sprint
        from naml.scheduler import Scheduler

        from tests.test_package import _build_sprint

        sprint_root = _build_sprint(
            self._tmp,
            slices=[{"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": []}],
        )
        sprint = load_sprint(sprint_root)

        state_mod.ensure_state_dir(sprint_root)
        held_status = state_mod.SliceStatus(slice_id="slice-1", state=states.HELD)
        state_mod.save_slice_status(sprint_root, held_status)
        # Sentinel got lost somehow (user `rm`'d it).
        self.assertFalse(state_mod.is_hold_requested(sprint_root, "slice-1"))

        Scheduler(sprint).absorb_existing_statuses(sprint_root)

        self.assertTrue(state_mod.is_hold_requested(sprint_root, "slice-1"))


class ProcessSliceHeldResumeTests(unittest.TestCase):
    """``process_slice`` must fast-forward past setup when the slice is already
    in ``held``: keep the existing worktree, keep the session_id, and resume
    the Claude session rather than starting a fresh one."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-held-resume-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_ctx_and_status(self) -> tuple[object, state_mod.SliceStatus, Path]:
        from naml.package import load_sprint
        from tests.test_package import _build_sprint

        sprint_root = _build_sprint(
            self._tmp,
            slices=[{"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": []}],
        )
        sprint = load_sprint(sprint_root)

        # Held slice with all the state a real previous run would have left.
        state_mod.ensure_state_dir(sprint_root)
        worktree = self._tmp / "wt-slice-1"
        worktree.mkdir()
        status = state_mod.SliceStatus(
            slice_id="slice-1",
            state=states.HELD,
            session_id="prev-session-uuid",
            worktree=str(worktree),
            branch="naml/sprint-A/slice-1",
        )
        status.record_transition(state=states.HELD, detail="user HOLD")
        state_mod.save_slice_status(sprint_root, status)
        state_mod.write_hold_requested(sprint_root, "slice-1")

        class _Cfg:
            repo = "owner/repo"
            repo_root = self._tmp
            base_branch = "main"
            worktree_symlinks = ()
            claude_bin = "claude"
            claude_config_dir = None
            run_cap_minutes = 1
            max_retries = 0
            model_context_max = 200_000
            gates: tuple = ()

        ctx = lane_mod.LaneContext(
            config=_Cfg(),
            sprint=sprint,
            sprint_root=sprint_root,
            lane_root=self._tmp / "lane-root",
            log_dir=self._tmp / "logs",
            stop_after="pr",
        )
        return ctx, status, sprint_root

    def test_held_resume_skips_setup_and_uses_existing_session(self) -> None:
        ctx, status, sprint_root = self._make_ctx_and_status()

        # Clear the sentinel ahead of time so the spin loop exits
        # immediately when the lane enters it.
        state_mod.clear_hold_requested(sprint_root, "slice-1")

        # Capture the args run_implementer was called with so we can
        # assert resume=True and session_id preservation.
        impl_calls: list[dict] = []

        def fake_run_implementer(**kwargs):
            impl_calls.append(kwargs)
            return lane_mod.RunResult(
                completed=True, timed_out=False, exit_code=0,
                usage=__import__("naml.claude", fromlist=["Usage"]).Usage(),
            )

        # Stub out the rest of the work loop so we don't need a real
        # worktree / git / gates.
        with mock.patch.object(lane_mod, "run_implementer", side_effect=fake_run_implementer), \
             mock.patch.object(lane_mod, "run_gates") as fake_gates, \
             mock.patch.object(lane_mod, "gitops") as fake_gitops:
            fake_gates.return_value = mock.Mock(passed=True, failed_gate=None, tail="")
            fake_gitops.diff_has_changes.return_value = True
            fake_gitops.push_branch.return_value = None
            fake_gitops.create_pr.return_value = "https://example/pr/1"

            final_state = lane_mod.process_slice("slice-1", ctx)

        # Did NOT re-run setup → worktree_add never called.
        fake_gitops.worktree_add.assert_not_called()
        # session_id preserved (no fresh UUID minted on top of it).
        self.assertEqual(status.session_id, "prev-session-uuid")
        # First implementer call used resume=True so claude --resume hits
        # the existing session, not --session-id which would 409.
        self.assertGreater(len(impl_calls), 0)
        self.assertTrue(impl_calls[0]["resume"], "first call must resume the held session")
        self.assertEqual(impl_calls[0]["session_id"], "prev-session-uuid")
        # No fresh display_name (that's only for new sessions).
        self.assertIsNone(impl_calls[0].get("display_name"))
        # Slice followed through to PR — held-resume is end-to-end, not a
        # dead-end.
        self.assertEqual(final_state, states.PR)
        # On disk, status reflects the final state.
        reloaded = state_mod.load_slice_status(sprint_root, "slice-1")
        assert reloaded is not None
        self.assertEqual(reloaded.state, states.PR)
        # session_id still the same on disk.
        self.assertEqual(reloaded.session_id, "prev-session-uuid")


class AppleScriptEscapeTests(unittest.TestCase):
    """``_applescript_double_quote_escape`` defends against worktree paths
    that contain literal ``"`` or ``\\`` from breaking the AppleScript
    used to launch Terminal.app. Today naml controls the path so this
    is purely defensive."""

    def test_no_special_chars_is_a_passthrough(self) -> None:
        self.assertEqual(
            server_mod._applescript_double_quote_escape("/Users/me/worktrees/slice-1"),
            "/Users/me/worktrees/slice-1",
        )

    def test_double_quotes_are_backslash_escaped(self) -> None:
        self.assertEqual(
            server_mod._applescript_double_quote_escape('a"b'),
            'a\\"b',
        )

    def test_backslashes_are_escaped_first(self) -> None:
        # If we escaped `"` first the backslash pass would then double up
        # the backslash we just added. Order matters.
        self.assertEqual(
            server_mod._applescript_double_quote_escape('a\\"b'),
            'a\\\\\\"b',
        )

    def test_combined(self) -> None:
        # A path Apple's docs themselves use as a torture test.
        self.assertEqual(
            server_mod._applescript_double_quote_escape('he said "hi\\there"'),
            'he said \\"hi\\\\there\\"',
        )


class InterveneEndpointUnitTests(unittest.TestCase):
    """Drive ``server._intervene_response`` directly — no event loop."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-intervene-")).resolve()
        sprints = self._tmp / ".naml" / "sprints"
        sprints.mkdir(parents=True)
        self._sprint_root = sprints / "sprint-A"
        self._sprint_root.mkdir()
        state_mod.ensure_state_dir(self._sprint_root)

        class _Cfg:
            pass

        self._cfg = _Cfg()
        self._cfg.repo_root = self._tmp
        self._cfg.sprints_path = sprints

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_status(
        self, state: str, *, worktree: str = "", session_id: str = "sess-x"
    ) -> state_mod.SliceStatus:
        status = state_mod.SliceStatus(
            slice_id="slice-1",
            state=state,
            session_id=session_id,
            worktree=worktree,
            branch="naml/sprint-A/slice-1",
        )
        state_mod.save_slice_status(self._sprint_root, status)
        return status

    def _body(self, response):
        import json as _json
        return _json.loads(response.body)

    def test_unknown_action_returns_400(self) -> None:
        self._write_status(states.WORK)
        resp = server_mod._intervene_response(self._cfg, "slice-1", "nuke")
        self.assertEqual(resp.status, 400)
        self.assertIn("unknown action", self._body(resp)["error"])

    def test_missing_action_returns_400(self) -> None:
        self._write_status(states.WORK)
        resp = server_mod._intervene_response(self._cfg, "slice-1", "")
        self.assertEqual(resp.status, 400)

    def test_unknown_slice_returns_404(self) -> None:
        resp = server_mod._intervene_response(self._cfg, "slice-nope", "hold")
        self.assertEqual(resp.status, 404)

    def test_hold_writes_sentinel(self) -> None:
        self._write_status(states.WORK)
        self.assertFalse(state_mod.is_hold_requested(self._sprint_root, "slice-1"))
        resp = server_mod._intervene_response(self._cfg, "slice-1", "hold")
        self.assertEqual(resp.status, 200)
        self.assertTrue(state_mod.is_hold_requested(self._sprint_root, "slice-1"))
        # Server intentionally does NOT flip the slice state — the lane
        # owns that transition (it runs once the current turn settles).
        on_disk = state_mod.load_slice_status(self._sprint_root, "slice-1")
        assert on_disk is not None
        self.assertEqual(on_disk.state, states.WORK)

    def test_hold_in_setup_is_accepted(self) -> None:
        # ``setup`` is in HOLDABLE_STATES so the lane will catch the
        # sentinel as soon as it enters work.
        self._write_status(states.SETUP)
        resp = server_mod._intervene_response(self._cfg, "slice-1", "hold")
        self.assertEqual(resp.status, 200)
        self.assertTrue(state_mod.is_hold_requested(self._sprint_root, "slice-1"))

    def test_hold_refused_in_pr_state(self) -> None:
        # PR is not held-able per the slice-14 spec — the lane is already
        # at rest there. Writing the sentinel would orphan it forever.
        self._write_status(states.PR)
        self.assertFalse(state_mod.is_hold_requested(self._sprint_root, "slice-1"))
        resp = server_mod._intervene_response(self._cfg, "slice-1", "hold")
        self.assertEqual(resp.status, 409)
        body = self._body(resp)
        self.assertEqual(body["current_state"], states.PR)
        self.assertIn("HOLD only applies", body["error"])
        # Crucially: no sentinel was written.
        self.assertFalse(state_mod.is_hold_requested(self._sprint_root, "slice-1"))

    def test_hold_refused_in_terminal_state(self) -> None:
        for state in (states.MERGED, states.FAILED, states.REVIEW, states.HELD):
            with self.subTest(state=state):
                self._write_status(state)
                resp = server_mod._intervene_response(
                    self._cfg, "slice-1", "hold"
                )
                self.assertEqual(resp.status, 409)
                self.assertFalse(
                    state_mod.is_hold_requested(self._sprint_root, "slice-1")
                )

    def test_resume_clears_sentinel(self) -> None:
        self._write_status(states.HELD)
        state_mod.write_hold_requested(self._sprint_root, "slice-1")
        resp = server_mod._intervene_response(self._cfg, "slice-1", "resume")
        self.assertEqual(resp.status, 200)
        self.assertFalse(state_mod.is_hold_requested(self._sprint_root, "slice-1"))

    def test_open_terminal_refused_in_work_state(self) -> None:
        self._write_status(states.WORK, worktree=str(self._tmp / "wt"))
        resp = server_mod._intervene_response(self._cfg, "slice-1", "open-terminal")
        self.assertEqual(resp.status, 409)
        body = self._body(resp)
        self.assertEqual(body["current_state"], states.WORK)
        self.assertIn("HOLD first", body["error"])

    def test_open_terminal_allowed_in_held_state(self) -> None:
        self._write_status(states.HELD, worktree=str(self._tmp / "wt"))
        # Force the Darwin branch on/off depending on host. We patch
        # ``platform.system`` AND the subprocess call so the test can pass
        # on Linux CI too.
        with mock.patch.object(server_mod.platform, "system", return_value="Darwin"), \
             mock.patch.object(server_mod, "_open_terminal_macos", return_value=True):
            resp = server_mod._intervene_response(
                self._cfg, "slice-1", "open-terminal"
            )
        self.assertEqual(resp.status, 200)
        self.assertEqual(self._body(resp)["worktree"], str(self._tmp / "wt"))

    def test_open_terminal_returns_501_on_non_darwin(self) -> None:
        self._write_status(states.HELD, worktree=str(self._tmp / "wt"))
        with mock.patch.object(server_mod.platform, "system", return_value="Linux"):
            resp = server_mod._intervene_response(
                self._cfg, "slice-1", "open-terminal"
            )
        self.assertEqual(resp.status, 501)

    def test_mark_failed_writes_transition(self) -> None:
        self._write_status(states.WORK)
        resp = server_mod._intervene_response(self._cfg, "slice-1", "mark-failed")
        self.assertEqual(resp.status, 200)
        on_disk = state_mod.load_slice_status(self._sprint_root, "slice-1")
        assert on_disk is not None
        self.assertEqual(on_disk.state, states.FAILED)
        self.assertEqual(on_disk.transitions[-1].state, states.FAILED)

    def test_skip_writes_abandoned_transition(self) -> None:
        self._write_status(states.HELD)
        state_mod.write_hold_requested(self._sprint_root, "slice-1")
        resp = server_mod._intervene_response(self._cfg, "slice-1", "skip")
        self.assertEqual(resp.status, 200)
        on_disk = state_mod.load_slice_status(self._sprint_root, "slice-1")
        assert on_disk is not None
        self.assertEqual(on_disk.state, states.ABANDONED)
        # Sentinel cleared so a stale RESUME can't restart the lane.
        self.assertFalse(state_mod.is_hold_requested(self._sprint_root, "slice-1"))


if __name__ == "__main__":
    unittest.main()
