"""Tests for naml.reconciler — external-merge promotion.

The reconciler is the bridge between a rescue (user clicks "Squash and
merge" on GitHub) and naml's state machine. These tests pin the
state-promotion logic with a fake ``pr_state_fn`` so we never touch
real gh during CI.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from naml import reconciler
from naml import state as state_mod
from naml import states


class _FakeSlice:
    def __init__(self, sid: str, deps: list[str] | None = None) -> None:
        self.id = sid
        self.depends_on = tuple(deps or [])
        self.title = sid


class _FakeSprint:
    def __init__(self, slices: list[_FakeSlice]) -> None:
        self.id = "2026-05-19-fake"
        self.slices = tuple(slices)
        self._by = {s.id: s for s in slices}

    def slice_by_id(self, sid: str) -> _FakeSlice:
        return self._by[sid]


class _FakeConfig:
    def __init__(self, tmp: Path) -> None:
        self.repo = "owner/repo"
        self.repo_root = tmp / "repo_root"
        self.repo_root.mkdir()


class ReconcilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-recon-")).resolve()
        self.cfg = _FakeConfig(self._tmp)
        self.sprint_root = self._tmp / "sprints" / "2026-05-19-fake"
        self.sprint_root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _seed(self, slice_id: str, state: str, branch: str = "") -> None:
        status = state_mod.SliceStatus(
            slice_id=slice_id,
            state=state,
            branch=branch or f"naml/2026-05-19-fake/{slice_id}",
        )
        state_mod.save_slice_status(self.sprint_root, status)

    def _seed_sprint(self, slice_states: dict[str, str]) -> None:
        sprint_state = state_mod.SprintState(
            sprint_id="2026-05-19-fake",
            state=states.SPRINT_MERGE_BLOCKED,
            slices=dict(slice_states),
        )
        state_mod.save_sprint_state(self.sprint_root, sprint_state)

    def test_promotes_review_passed_when_pr_merged(self) -> None:
        self._seed("slice-1", states.REVIEW_PASSED)
        self._seed("slice-2", states.REVIEW_PASSED)
        self._seed_sprint({"slice-1": states.REVIEW_PASSED, "slice-2": states.REVIEW_PASSED})
        sprint = _FakeSprint([_FakeSlice("slice-1"), _FakeSlice("slice-2")])

        def fake_pr_state(branch, *, cwd, repo):  # noqa: ARG001
            return "MERGED" if "slice-1" in branch else "OPEN"

        report = reconciler.reconcile_sprint(
            sprint, self.cfg, self.sprint_root, pr_state_fn=fake_pr_state
        )

        self.assertEqual(report.changed_count, 1)
        self.assertEqual(report.outcomes[0].slice_id, "slice-1")
        self.assertEqual(report.outcomes[0].new_state, states.MERGED)
        # On-disk status reflects the promotion.
        s1 = state_mod.load_slice_status(self.sprint_root, "slice-1")
        s2 = state_mod.load_slice_status(self.sprint_root, "slice-2")
        self.assertEqual(s1.state, states.MERGED)
        self.assertEqual(s2.state, states.REVIEW_PASSED)
        # Sprint's slices map mirrors the new truth.
        sprint_state = state_mod.load_sprint_state(self.sprint_root)
        self.assertEqual(sprint_state.slices["slice-1"], states.MERGED)
        self.assertEqual(sprint_state.slices["slice-2"], states.REVIEW_PASSED)

    def test_idempotent_when_pr_already_merged_state(self) -> None:
        self._seed("slice-1", states.MERGED)
        sprint = _FakeSprint([_FakeSlice("slice-1")])
        called: list[str] = []

        def fake_pr_state(branch, *, cwd, repo):  # noqa: ARG001
            called.append(branch)
            return "MERGED"

        report = reconciler.reconcile_sprint(
            sprint, self.cfg, self.sprint_root, pr_state_fn=fake_pr_state
        )

        self.assertEqual(report.changed_count, 0)
        # Already-merged slice should not be asked about — saves API calls.
        self.assertEqual(called, [])

    def test_refreshes_stale_sprint_slices_map(self) -> None:
        # Per-slice files all say merged; sprint.json's slices map is stale
        # (e.g. last write happened mid-rescue). Reconciler must refresh
        # the rollup even when zero promotions happen this run.
        self._seed("slice-1", states.MERGED)
        self._seed("slice-2", states.MERGED)
        # Stale sprint state — slices map disagrees with per-slice files.
        self._seed_sprint({"slice-1": states.MERGED, "slice-2": states.REVIEW_PASSED})
        sprint = _FakeSprint([_FakeSlice("slice-1"), _FakeSlice("slice-2")])

        report = reconciler.reconcile_sprint(
            sprint, self.cfg, self.sprint_root,
            pr_state_fn=lambda branch, *, cwd, repo: "MERGED",
        )

        self.assertEqual(report.changed_count, 0)
        sprint_state = state_mod.load_sprint_state(self.sprint_root)
        # Rollup now matches the per-slice truth.
        self.assertEqual(sprint_state.slices["slice-2"], states.MERGED)
        self.assertEqual(sprint_state.state, states.SPRINT_COMPLETE)

    def test_skips_pending_and_setup_slices(self) -> None:
        self._seed("slice-1", states.PENDING)
        self._seed("slice-2", states.SETUP)
        self._seed("slice-3", states.WORK)
        sprint = _FakeSprint(
            [_FakeSlice("slice-1"), _FakeSlice("slice-2"), _FakeSlice("slice-3")]
        )

        def fake_pr_state(branch, *, cwd, repo):  # noqa: ARG001
            return "MERGED"

        report = reconciler.reconcile_sprint(
            sprint, self.cfg, self.sprint_root, pr_state_fn=fake_pr_state
        )
        # None should be promoted — work-in-progress slices stay where they are.
        self.assertEqual(report.changed_count, 0)

    def test_promotes_failed_when_pr_merged(self) -> None:
        # A slice can land at `failed` after a rescue (e.g. local gate
        # flake) but still have its PR merged manually. Reconcile should
        # surface this rather than leaving the cockpit lying.
        self._seed("slice-1", states.FAILED)
        sprint = _FakeSprint([_FakeSlice("slice-1")])

        report = reconciler.reconcile_sprint(
            sprint, self.cfg, self.sprint_root,
            pr_state_fn=lambda branch, *, cwd, repo: "MERGED",
        )
        self.assertEqual(report.changed_count, 1)
        s1 = state_mod.load_slice_status(self.sprint_root, "slice-1")
        self.assertEqual(s1.state, states.MERGED)
        # Detail string carries the prior state for forensics.
        self.assertIn("was failed", s1.transitions[-1].detail)

    def test_rolls_sprint_to_complete_when_all_merged(self) -> None:
        self._seed("slice-1", states.REVIEW_PASSED)
        self._seed("slice-2", states.REVIEW_PASSED)
        self._seed_sprint({"slice-1": states.REVIEW_PASSED, "slice-2": states.REVIEW_PASSED})
        sprint = _FakeSprint([_FakeSlice("slice-1"), _FakeSlice("slice-2")])

        reconciler.reconcile_sprint(
            sprint, self.cfg, self.sprint_root,
            pr_state_fn=lambda branch, *, cwd, repo: "MERGED",
        )
        sprint_state = state_mod.load_sprint_state(self.sprint_root)
        self.assertEqual(sprint_state.state, states.SPRINT_COMPLETE)

    def test_does_not_roll_sprint_when_some_slices_still_open(self) -> None:
        self._seed("slice-1", states.REVIEW_PASSED)
        self._seed("slice-2", states.REVIEW_PASSED)
        self._seed_sprint({"slice-1": states.REVIEW_PASSED, "slice-2": states.REVIEW_PASSED})
        sprint = _FakeSprint([_FakeSlice("slice-1"), _FakeSlice("slice-2")])

        def fake_pr_state(branch, *, cwd, repo):  # noqa: ARG001
            return "MERGED" if "slice-1" in branch else "OPEN"

        reconciler.reconcile_sprint(
            sprint, self.cfg, self.sprint_root, pr_state_fn=fake_pr_state
        )
        sprint_state = state_mod.load_sprint_state(self.sprint_root)
        # Not all merged → sprint state untouched.
        self.assertEqual(sprint_state.state, states.SPRINT_MERGE_BLOCKED)


if __name__ == "__main__":
    unittest.main()
