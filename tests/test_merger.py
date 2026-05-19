"""Tests for naml.merger — tier sequencing + sprint rollup.

Most of the merger's work is gitops / Claude subprocess plumbing; those
require a real GitHub repo and tokens to exercise. Here we test the
deterministic seams: state transitions, merge log persistence, tier-cap
gating, topological order, blocked-upstream cascade.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from naml import merger
from naml import state as state_mod
from naml import states


class MergeLogPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-mlog-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_append_then_load(self) -> None:
        e1 = state_mod.MergeLogEntry(
            slice_id="slice-1", pr_url="https://x/1", branch="b1",
            tier=1, success=True, detail="merged Tier 1",
            started_at="2026-05-19T10:00:00+00:00",
            finished_at="2026-05-19T10:00:05+00:00",
            duration_seconds=5.0,
        )
        e2 = state_mod.MergeLogEntry(
            slice_id="slice-2", pr_url="https://x/2", branch="b2",
            tier=4, success=False, detail="escalated to human",
            started_at="2026-05-19T10:00:05+00:00",
            finished_at="2026-05-19T10:00:10+00:00",
            duration_seconds=5.0,
        )
        state_mod.append_merge_log(self._tmp, e1)
        state_mod.append_merge_log(self._tmp, e2)
        loaded = state_mod.load_merge_log(self._tmp)
        self.assertEqual([e.slice_id for e in loaded], ["slice-1", "slice-2"])
        self.assertTrue(loaded[0].success)
        self.assertFalse(loaded[1].success)
        self.assertEqual(loaded[1].tier, 4)

    def test_load_missing_returns_empty(self) -> None:
        self.assertEqual(state_mod.load_merge_log(self._tmp), [])

    def test_load_malformed_returns_empty(self) -> None:
        p = self._tmp / "state" / "merge-log.json"
        p.parent.mkdir(parents=True)
        p.write_text("not json")
        self.assertEqual(state_mod.load_merge_log(self._tmp), [])


# ---  topological + skip / cascade behavior ------------------------------


class _FakeSlice:
    def __init__(self, sid: str, deps: list[str] | None = None) -> None:
        self.id = sid
        self.depends_on = tuple(deps or [])
        self.title = sid
        self.type = "AFK"
        self.touches = ()
        self.prompt_body = ""
        self.adrs = ()


class _FakeSprint:
    def __init__(self, slices: list[_FakeSlice]) -> None:
        self.id = "2026-05-19-fake"
        self.title = "Fake"
        self.target_repo = "owner/repo"
        self.base_branch = "main"
        self.kind = "greenfield"
        self.parent_sprint = None
        self.overview = "fake sprint"
        self.slices = tuple(slices)
        self._by = {s.id: s for s in slices}

    def slice_by_id(self, sid: str) -> _FakeSlice:
        return self._by[sid]

    def topological_order(self) -> list[str]:
        # Trivial topo: slices already in dep order in tests.
        return [s.id for s in self.slices]


class _FakeConfig:
    def __init__(self, tmp: Path) -> None:
        self.repo = "owner/repo"
        self.repo_root = tmp / "repo_root"
        self.repo_root.mkdir()
        self.base_branch = "main"
        self.gates = []
        self.claude_bin = "claude"
        self.claude_config_dir = None
        self.sprints_path = tmp / "sprints"
        self.sprints_path.mkdir()
        self.log_dir = tmp / "logs"


class TopologicalAndSkipTests(unittest.TestCase):
    """End-to-end of merge_sprint with every per-slice merge stubbed out."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-merger-")).resolve()
        self.cfg = _FakeConfig(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _seed_status(self, slice_id: str, state: str) -> None:
        sprint_root = self.cfg.sprints_path / "2026-05-19-fake"
        sprint_root.mkdir(parents=True, exist_ok=True)
        status = state_mod.SliceStatus(
            slice_id=slice_id,
            state=state,
            branch=f"naml/2026-05-19-fake/{slice_id}",
            worktree=str(self._tmp / "wt" / slice_id),
            pr_url=f"https://example/pr/{slice_id}",
        )
        state_mod.save_slice_status(sprint_root, status)

    def test_merges_in_topo_order_and_skips_non_review_passed(self) -> None:
        sprint = _FakeSprint([
            _FakeSlice("slice-1"),
            _FakeSlice("slice-2", ["slice-1"]),
            _FakeSlice("slice-3", ["slice-1"]),
        ])
        # slice-1 review_passed, slice-2 already merged, slice-3 review_passed.
        self._seed_status("slice-1", states.REVIEW_PASSED)
        self._seed_status("slice-2", states.MERGED)
        self._seed_status("slice-3", states.REVIEW_PASSED)

        called: list[str] = []

        def fake_run(self_, *, tier_cap: int):  # noqa: ARG001
            called.append(self_.slice.id)
            # Pretend Tier 1 succeeded.
            sprint_root = self_.sprint_root
            state_mod.save_slice_status(
                sprint_root,
                state_mod.SliceStatus(
                    slice_id=self_.slice.id,
                    state=states.MERGED,
                ),
            )
            return merger.MergeOutcome(
                slice_id=self_.slice.id,
                pr_url=self_.status.pr_url,
                branch=self_.branch,
                tier=1,
                success=True,
                detail="merged",
                started_at="2026-05-19T10:00:00+00:00",
                finished_at="2026-05-19T10:00:01+00:00",
                duration_seconds=1.0,
            )

        with patch.object(merger._SliceMerger, "run", fake_run):
            report = merger.merge_sprint(sprint, self.cfg)

        self.assertEqual(called, ["slice-1", "slice-3"])  # slice-2 skipped
        self.assertEqual(report.merged_count(), 2)
        self.assertEqual(report.blocked_count(), 0)

    def test_blocked_slice_cascades_to_dependents(self) -> None:
        sprint = _FakeSprint([
            _FakeSlice("slice-1"),
            _FakeSlice("slice-2", ["slice-1"]),
        ])
        self._seed_status("slice-1", states.REVIEW_PASSED)
        self._seed_status("slice-2", states.REVIEW_PASSED)

        called: list[str] = []

        def fake_run(self_, *, tier_cap: int):  # noqa: ARG001
            called.append(self_.slice.id)
            # Block slice-1 at Tier 4.
            return merger.MergeOutcome(
                slice_id=self_.slice.id,
                pr_url=self_.status.pr_url,
                branch=self_.branch,
                tier=4, success=False, detail="conflict",
                started_at="2026-05-19T10:00:00+00:00",
                finished_at="2026-05-19T10:00:01+00:00",
                duration_seconds=1.0,
            )

        with patch.object(merger._SliceMerger, "run", fake_run):
            report = merger.merge_sprint(sprint, self.cfg)

        # slice-2 must NOT be attempted because slice-1 is blocked.
        self.assertEqual(called, ["slice-1"])
        self.assertEqual(report.merged_count(), 0)
        self.assertEqual(report.blocked_count(), 1)

    def test_already_merged_in_lane_failed_skipped(self) -> None:
        sprint = _FakeSprint([
            _FakeSlice("slice-1"),
            _FakeSlice("slice-2"),
        ])
        self._seed_status("slice-1", states.NEEDS_HUMAN_REVIEW)
        self._seed_status("slice-2", states.REVIEW_PASSED)

        called: list[str] = []

        def fake_run(self_, *, tier_cap: int):  # noqa: ARG001
            called.append(self_.slice.id)
            return merger.MergeOutcome(
                slice_id=self_.slice.id, pr_url="", branch="b",
                tier=1, success=True, detail="ok",
                started_at="", finished_at="", duration_seconds=0.0,
            )

        with patch.object(merger._SliceMerger, "run", fake_run):
            merger.merge_sprint(sprint, self.cfg)

        # slice-1 in NEEDS_HUMAN_REVIEW must skip.
        self.assertEqual(called, ["slice-2"])

    def test_tier_cap_posts_pr_comment(self) -> None:
        """A tier_cap-induced merge_blocked must post the PR comment too —
        not just transition state. Same human-escalation contract as a
        full Tier-4 escalation."""
        from naml import merger as merger_mod

        comments: list[tuple[str, str]] = []

        def fake_pr_comment(branch, body, *, cwd, repo):  # noqa: ARG001
            comments.append((branch, body))

        # Stand up a minimal SliceMerger + drive _fail_with_blocked.
        sprint = _FakeSprint([_FakeSlice("slice-x")])
        self._seed_status("slice-x", states.MERGING)
        sprint_root = self.cfg.sprints_path / "2026-05-19-fake"
        status = state_mod.load_slice_status(sprint_root, "slice-x")
        assert status is not None
        m = merger_mod._SliceMerger(
            sprint=sprint,
            cfg=self.cfg,
            sprint_root=sprint_root,
            log_dir=self._tmp / "merger-log",
            status=status,
            slice_=sprint.slices[0],
        )
        m.tier_history = [
            (1, "rebase paused on conflicts"),
            (2, "no scripted resolver for `cli.py`"),
        ]

        with patch("naml.merger.gitops.pr_comment", fake_pr_comment), \
             patch("naml.merger.gitops.rebase_abort", lambda **_: None):
            outcome = m._fail_with_blocked(
                "no scripted resolver for `cli.py`",
                "2026-05-19T11:00:00+00:00",
                0.0,
                last_tier=2,
            )

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.tier, 2)
        self.assertEqual(len(comments), 1)
        _branch, body = comments[0]
        self.assertIn("merge_blocked on `slice-x`", body)
        # Tier-by-tier history is present.
        self.assertIn("Tier 1", body)
        self.assertIn("rebase paused", body)
        self.assertIn("Tier 2", body)
        self.assertIn("no scripted resolver", body)

    def test_merging_slice_is_resumable(self) -> None:
        """A slice stuck in MERGING from a prior interrupted run should be
        retried (not silently skipped like MERGED, and not log-skipped like
        FAILED). The retry restarts at Tier 1."""
        sprint = _FakeSprint([_FakeSlice("slice-1"), _FakeSlice("slice-2")])
        # slice-1 already merged, slice-2 left in MERGING by a prior run.
        self._seed_status("slice-1", states.MERGED)
        self._seed_status("slice-2", states.MERGING)

        called: list[str] = []

        def fake_run(self_, *, tier_cap: int):  # noqa: ARG001
            called.append(self_.slice.id)
            return merger.MergeOutcome(
                slice_id=self_.slice.id,
                pr_url=self_.status.pr_url,
                branch=self_.branch,
                tier=1, success=True, detail="merged on retry",
                started_at="2026-05-19T11:00:00+00:00",
                finished_at="2026-05-19T11:00:01+00:00",
                duration_seconds=1.0,
            )

        with patch.object(merger._SliceMerger, "run", fake_run):
            report = merger.merge_sprint(sprint, self.cfg)

        # slice-1 silently skipped (MERGED), slice-2 retried.
        self.assertEqual(called, ["slice-2"])
        self.assertEqual(report.merged_count(), 1)

    def test_sprint_state_transitions(self) -> None:
        sprint = _FakeSprint([_FakeSlice("slice-1")])
        self._seed_status("slice-1", states.REVIEW_PASSED)

        def fake_run(self_, *, tier_cap: int):  # noqa: ARG001
            sprint_root = self_.sprint_root
            state_mod.save_slice_status(
                sprint_root,
                state_mod.SliceStatus(
                    slice_id=self_.slice.id,
                    state=states.MERGED,
                ),
            )
            return merger.MergeOutcome(
                slice_id=self_.slice.id, pr_url="", branch="",
                tier=1, success=True, detail="",
                started_at="", finished_at="", duration_seconds=0.0,
            )

        with patch.object(merger._SliceMerger, "run", fake_run):
            report = merger.merge_sprint(sprint, self.cfg)

        sprint_root = self.cfg.sprints_path / "2026-05-19-fake"
        sprint_state = state_mod.load_sprint_state(sprint_root)
        assert sprint_state is not None
        self.assertEqual(sprint_state.state, states.SPRINT_COMPLETE)
        # At least 2 sprint-level transitions recorded (merging → complete).
        self.assertTrue(
            any(t.state == states.SPRINT_MERGING for t in sprint_state.transitions)
        )
        self.assertTrue(
            any(t.state == states.SPRINT_COMPLETE for t in sprint_state.transitions)
        )
        self.assertEqual(report.sprint_state, states.SPRINT_COMPLETE)


if __name__ == "__main__":
    unittest.main()
