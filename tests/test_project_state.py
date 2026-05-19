"""Tests for ``naml.project_state``."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from naml import project_state as ps


class ProjectStateLoadTests(unittest.TestCase):
    def test_fresh_load_returns_idle_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = ps.load_project_state(Path(td) / ".naml")
            self.assertEqual(state.state, ps.IDLE)
            self.assertEqual(state.current_sprint, "")
            self.assertEqual(state.queued_sprints, [])
            self.assertEqual(state.metrics.sprints_started, 0)
            self.assertEqual(state.transitions, [])

    def test_load_malformed_json_returns_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            naml.mkdir()
            (naml / "state.json").write_text("{not valid json", encoding="utf-8")

            state = ps.load_project_state(naml)
            self.assertEqual(state.state, ps.IDLE)

    def test_load_unknown_state_value_normalizes_to_idle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            naml.mkdir()
            (naml / "state.json").write_text(
                json.dumps({"state": "exploding", "current_sprint": "s1"}),
                encoding="utf-8",
            )

            state = ps.load_project_state(naml)
            self.assertEqual(state.state, ps.IDLE)
            self.assertEqual(state.current_sprint, "s1")

    def test_load_filters_non_string_queued_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            naml.mkdir()
            (naml / "state.json").write_text(
                json.dumps(
                    {
                        "state": "idle",
                        "queued_sprints": ["s1", 42, None, "s2"],
                    }
                ),
                encoding="utf-8",
            )

            state = ps.load_project_state(naml)
            self.assertEqual(state.queued_sprints, ["s1", "s2"])


class ProjectStateRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            state = ps.ProjectState(
                state=ps.ACTIVE,
                current_sprint="2026-05-19-foo",
                queued_sprints=["2026-05-20-bar"],
                metrics=ps.ProjectMetrics(
                    sprints_started=3,
                    sprints_completed=2,
                    slices_merged=7,
                ),
            )
            state.record_transition(state=ps.ACTIVE, detail="sprint foo started")

            ps.save_project_state(naml, state)

            loaded = ps.load_project_state(naml)
            self.assertEqual(loaded.state, ps.ACTIVE)
            self.assertEqual(loaded.current_sprint, "2026-05-19-foo")
            self.assertEqual(loaded.queued_sprints, ["2026-05-20-bar"])
            self.assertEqual(loaded.metrics.sprints_started, 3)
            self.assertEqual(loaded.metrics.sprints_completed, 2)
            self.assertEqual(loaded.metrics.slices_merged, 7)
            self.assertEqual(len(loaded.transitions), 1)
            self.assertEqual(loaded.transitions[0].state, ps.ACTIVE)
            self.assertEqual(loaded.transitions[0].detail, "sprint foo started")

    def test_save_creates_naml_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            self.assertFalse(naml.exists())

            ps.save_project_state(naml, ps.ProjectState())
            self.assertTrue(naml.is_dir())
            self.assertTrue((naml / "state.json").is_file())

    def test_save_is_atomic_no_tmp_leftover(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            ps.save_project_state(naml, ps.ProjectState(state=ps.PAUSED))

            leftovers = [p.name for p in naml.iterdir() if ".tmp." in p.name]
            self.assertEqual(leftovers, [])


class TransitionTests(unittest.TestCase):
    def test_record_transition_rejects_unknown_state(self) -> None:
        state = ps.ProjectState()
        with self.assertRaises(ValueError):
            state.record_transition(state="zomg")

    def test_record_transition_appends_iso_timestamp(self) -> None:
        state = ps.ProjectState()
        state.record_transition(state=ps.ACTIVE, detail="started sprint")

        self.assertEqual(len(state.transitions), 1)
        t = state.transitions[0]
        self.assertEqual(t.state, ps.ACTIVE)
        self.assertEqual(t.detail, "started sprint")
        # Microsecond ISO timestamp -- shouldn't be empty, should parse.
        self.assertTrue(t.entered_at)
        self.assertIsNotNone(t.entered_dt)

    def test_duration_in_sums_segments(self) -> None:
        state = ps.ProjectState()
        state.record_transition(state=ps.ACTIVE, detail="sprint A")
        time.sleep(0.01)
        state.record_transition(state=ps.AWAITING_HUMAN, detail="merge blocked")
        time.sleep(0.01)
        state.record_transition(state=ps.ACTIVE, detail="unblocked")
        time.sleep(0.01)
        state.record_transition(state=ps.IDLE, detail="sprint complete")

        # ACTIVE was entered twice; the helper must sum both segments.
        active_duration = state.duration_in(ps.ACTIVE)
        self.assertIsNotNone(active_duration)
        assert active_duration is not None
        self.assertGreater(active_duration.total_seconds(), 0)


class _StubCfg:
    """Minimal duck-typed NamlConfig for hierarchy tests."""

    def __init__(self, repo_root: Path, sprints_path: Path) -> None:
        self.repo_root = repo_root
        self.sprints_path = sprints_path


class TransitionHelpersTests(unittest.TestCase):
    def test_on_sprint_start_from_idle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            state = ps.on_sprint_start(naml, "sprint-A")

            self.assertEqual(state.state, ps.ACTIVE)
            self.assertEqual(state.current_sprint, "sprint-A")
            self.assertEqual(state.metrics.sprints_started, 1)
            self.assertEqual(len(state.transitions), 1)
            self.assertEqual(state.transitions[-1].state, ps.ACTIVE)

    def test_on_sprint_start_is_idempotent_for_same_sprint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            ps.on_sprint_start(naml, "sprint-A")
            state = ps.on_sprint_start(naml, "sprint-A")

            # Second call must not double-count or append a redundant transition.
            self.assertEqual(state.metrics.sprints_started, 1)
            self.assertEqual(len(state.transitions), 1)

    def test_on_sprint_start_new_sprint_bumps_counter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            ps.on_sprint_start(naml, "sprint-A")
            # Simulate sprint-A completing (idle), then sprint-B starting.
            ps.on_sprint_merge_finished(
                naml,
                "sprint-A",
                sprint_state="complete",
                merged_count=1,
                blocked_count=0,
            )
            state = ps.on_sprint_start(naml, "sprint-B")

            self.assertEqual(state.current_sprint, "sprint-B")
            self.assertEqual(state.metrics.sprints_started, 2)
            self.assertEqual(state.state, ps.ACTIVE)

    def test_on_run_finished_partial_failure_to_awaiting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            ps.on_sprint_start(naml, "sprint-A")
            state = ps.on_sprint_run_finished(
                naml, "sprint-A", aggregate_state="partial_failure"
            )

            self.assertEqual(state.state, ps.AWAITING_HUMAN)

    def test_on_run_finished_clean_stays_active(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            ps.on_sprint_start(naml, "sprint-A")
            state = ps.on_sprint_run_finished(
                naml, "sprint-A", aggregate_state="awaiting_signoff"
            )

            # Sprint is mid-pipeline (waiting for merge phase). Project
            # stays active.
            self.assertEqual(state.state, ps.ACTIVE)

    def test_on_merge_finished_complete_to_idle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            ps.on_sprint_start(naml, "sprint-A")
            state = ps.on_sprint_merge_finished(
                naml,
                "sprint-A",
                sprint_state="complete",
                merged_count=3,
                blocked_count=0,
            )

            self.assertEqual(state.state, ps.IDLE)
            self.assertEqual(state.current_sprint, "")
            self.assertEqual(state.metrics.sprints_completed, 1)
            self.assertEqual(state.metrics.slices_merged, 3)

    def test_on_merge_finished_blocked_to_awaiting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            naml = Path(td) / ".naml"
            ps.on_sprint_start(naml, "sprint-A")
            state = ps.on_sprint_merge_finished(
                naml,
                "sprint-A",
                sprint_state="merge_blocked",
                merged_count=1,
                blocked_count=2,
            )

            self.assertEqual(state.state, ps.AWAITING_HUMAN)
            self.assertEqual(state.current_sprint, "sprint-A")
            self.assertEqual(state.metrics.slices_merged, 1)
            self.assertEqual(state.metrics.slices_blocked, 2)
            self.assertEqual(state.metrics.sprints_completed, 0)


class BuildHierarchyTests(unittest.TestCase):
    def test_hierarchy_with_no_current_sprint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            sprints = root / ".naml" / "sprints"
            sprints.mkdir(parents=True)
            cfg = _StubCfg(repo_root=root, sprints_path=sprints)

            payload = ps.build_hierarchy(cfg)
            self.assertEqual(payload["project"]["state"], ps.IDLE)
            self.assertIsNone(payload["current_sprint"])
            self.assertEqual(payload["lanes"], [])

    def test_hierarchy_with_current_sprint(self) -> None:
        from naml import state as state_mod

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            sprints = root / ".naml" / "sprints"
            sprint_root = sprints / "sprint-A"
            (sprint_root / "state").mkdir(parents=True)
            cfg = _StubCfg(repo_root=root, sprints_path=sprints)

            ps.on_sprint_start(root / ".naml", "sprint-A")

            sprint_state = state_mod.SprintState(
                sprint_id="sprint-A",
                state="executing",
                lanes_configured=2,
                lanes_effective=2,
                slices={"slice-1": "merged", "slice-2": "pr"},
            )
            sprint_state.record_transition(state="executing", detail="started")
            state_mod.save_sprint_state(sprint_root, sprint_state)

            slice_status = state_mod.SliceStatus(slice_id="slice-1", state="merged")
            slice_status.record_transition(state="merged", detail="ok")
            state_mod.save_slice_status(sprint_root, slice_status)

            payload = ps.build_hierarchy(cfg)
            self.assertEqual(payload["project"]["state"], ps.ACTIVE)
            self.assertIsNotNone(payload["current_sprint"])
            assert payload["current_sprint"] is not None  # for mypy
            self.assertEqual(payload["current_sprint"]["sprint_id"], "sprint-A")
            self.assertEqual(len(payload["current_sprint"]["slices"]), 2)
            slice_ids = {s["slice_id"] for s in payload["current_sprint"]["slices"]}
            self.assertEqual(slice_ids, {"slice-1", "slice-2"})


if __name__ == "__main__":
    unittest.main()
