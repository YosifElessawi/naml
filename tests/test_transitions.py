"""Tests for the transition-history fields on SliceStatus + SprintState."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from naml.state import (
    SliceStatus,
    SprintState,
    Transition,
    load_slice_status,
    load_sprint_state,
    save_slice_status,
    save_sprint_state,
)


class _Tmp:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-trans-")).resolve()

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)


class RecordTransitionTests(_Tmp, unittest.TestCase):
    def test_records_state_and_isoformat_timestamp(self) -> None:
        status = SliceStatus(slice_id="slice-1")
        status.record_transition(state="setup", detail="lane 1")
        self.assertEqual(len(status.transitions), 1)
        t = status.transitions[0]
        self.assertEqual(t.state, "setup")
        self.assertEqual(t.detail, "lane 1")
        # ISO 8601 + timezone-aware UTC.
        dt = datetime.fromisoformat(t.entered_at)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_transitions_append_only(self) -> None:
        status = SliceStatus(slice_id="slice-1")
        status.record_transition(state="setup")
        status.record_transition(state="work")
        status.record_transition(state="pr")
        self.assertEqual(
            [t.state for t in status.transitions], ["setup", "work", "pr"]
        )

    def test_sprint_state_record_transition(self) -> None:
        sprint = SprintState(sprint_id="2026-05-19-x")
        sprint.record_transition(state="package_received")
        sprint.record_transition(state="executing")
        self.assertEqual(
            [t.state for t in sprint.transitions],
            ["package_received", "executing"],
        )


class RoundtripTests(_Tmp, unittest.TestCase):
    def test_slice_status_roundtrip_keeps_transitions(self) -> None:
        status = SliceStatus(slice_id="slice-1", state="work")
        status.record_transition(state="setup", detail="lane 1")
        status.record_transition(state="work")
        save_slice_status(self._tmp, status)
        loaded = load_slice_status(self._tmp, "slice-1")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(len(loaded.transitions), 2)
        self.assertEqual(loaded.transitions[0].state, "setup")
        self.assertEqual(loaded.transitions[0].detail, "lane 1")
        self.assertEqual(loaded.transitions[1].state, "work")

    def test_sprint_state_roundtrip_keeps_transitions(self) -> None:
        sprint = SprintState(sprint_id="x", state="executing")
        sprint.record_transition(state="package_received")
        sprint.record_transition(state="executing", detail="DAG built")
        save_sprint_state(self._tmp, sprint)
        loaded = load_sprint_state(self._tmp)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual([t.state for t in loaded.transitions],
                         ["package_received", "executing"])

    def test_legacy_status_without_transitions_loads_clean(self) -> None:
        """Old state files (no transitions key) must still load."""
        path = self._tmp / "state" / "slice-old.status.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "slice_id": "slice-old",
            "state": "pr",
            "session_id": "abc",
            "branch": "x",
            "worktree": "/tmp/wt",
            "pr_url": "https://example/pr/1",
            "attempts": {},
            "last_error": "",
            "review_verdict": "",
            # NOTE: no "transitions" field
        }))
        loaded = load_slice_status(self._tmp, "slice-old")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.state, "pr")
        self.assertEqual(loaded.transitions, [])

    def test_malformed_transitions_silently_drop(self) -> None:
        """Garbage in the transitions array must not crash load."""
        path = self._tmp / "state" / "slice-bad.status.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "slice_id": "slice-bad",
            "state": "pr",
            "transitions": [
                {"state": "setup", "entered_at": "2026-05-19T08:00:00+00:00"},
                "not-a-dict",
                {"missing": "state"},
                {"state": "work", "entered_at": "2026-05-19T08:01:00+00:00",
                 "detail": "ok"},
            ],
        }))
        loaded = load_slice_status(self._tmp, "slice-bad")
        assert loaded is not None
        # Two well-formed entries survive; garbage dropped.
        self.assertEqual([t.state for t in loaded.transitions], ["setup", "work"])


class DerivedHelperTests(unittest.TestCase):
    def _build(self, *moments: tuple[str, str, str]) -> SliceStatus:
        """Build a status with explicit transitions (state, iso, detail)."""
        s = SliceStatus(slice_id="x")
        for state, iso, detail in moments:
            s.transitions.append(Transition(state=state, entered_at=iso, detail=detail))
        return s

    def test_entered_at_returns_first_entry(self) -> None:
        s = self._build(
            ("setup", "2026-05-19T08:00:00+00:00", ""),
            ("work", "2026-05-19T08:01:00+00:00", ""),
            ("work", "2026-05-19T08:05:00+00:00", "review re-entered work"),
        )
        first = s.entered_at("work")
        assert first is not None
        self.assertEqual(first, datetime(2026, 5, 19, 8, 1, tzinfo=timezone.utc))

    def test_last_entered_at_returns_most_recent_entry(self) -> None:
        s = self._build(
            ("work", "2026-05-19T08:01:00+00:00", ""),
            ("work", "2026-05-19T08:05:00+00:00", "re-entered"),
        )
        latest = s.last_entered_at("work")
        assert latest is not None
        self.assertEqual(latest, datetime(2026, 5, 19, 8, 5, tzinfo=timezone.utc))

    def test_entered_at_returns_none_for_unvisited(self) -> None:
        s = self._build(("setup", "2026-05-19T08:00:00+00:00", ""))
        self.assertIsNone(s.entered_at("merged"))

    def test_duration_in_sums_across_re_entries(self) -> None:
        s = self._build(
            ("work", "2026-05-19T08:00:00+00:00", ""),
            ("pr", "2026-05-19T08:05:00+00:00", ""),     # 5m in work
            ("review", "2026-05-19T08:06:00+00:00", ""),
            ("work", "2026-05-19T08:10:00+00:00", "review fix"),
            ("pr", "2026-05-19T08:12:00+00:00", ""),     # 2m in work (second pass)
        )
        d = s.duration_in("work")
        self.assertEqual(d, timedelta(minutes=7))

    def test_duration_in_excludes_current_state(self) -> None:
        # Last transition is "work" — no successor yet, so it doesn't count.
        s = self._build(
            ("work", "2026-05-19T08:00:00+00:00", ""),
        )
        self.assertIsNone(s.duration_in("work"))

    def test_total_duration(self) -> None:
        s = self._build(
            ("setup", "2026-05-19T08:00:00+00:00", ""),
            ("work", "2026-05-19T08:01:00+00:00", ""),
            ("review_passed", "2026-05-19T08:10:00+00:00", ""),
        )
        self.assertEqual(s.total_duration(), timedelta(minutes=10))

    def test_total_duration_none_when_too_few(self) -> None:
        s = SliceStatus(slice_id="x")
        self.assertIsNone(s.total_duration())
        s.transitions.append(
            Transition(state="setup", entered_at="2026-05-19T08:00:00+00:00")
        )
        self.assertIsNone(s.total_duration())

    def test_malformed_isodate_returns_none(self) -> None:
        s = self._build(
            ("setup", "garbage", ""),
            ("work", "also garbage", ""),
        )
        self.assertIsNone(s.duration_in("setup"))
        self.assertIsNone(s.total_duration())


class IntegrationTests(_Tmp, unittest.TestCase):
    """Sanity: a SliceStatus that goes through real time-spaced record_transition
    calls should produce monotonically increasing timestamps."""

    def test_real_clock_progression(self) -> None:
        s = SliceStatus(slice_id="x")
        s.record_transition(state="setup")
        time.sleep(0.05)
        s.record_transition(state="work")
        time.sleep(0.05)
        s.record_transition(state="pr")
        ts = [datetime.fromisoformat(t.entered_at) for t in s.transitions]
        self.assertEqual(len(ts), 3)
        self.assertLess(ts[0], ts[1])
        self.assertLess(ts[1], ts[2])

        # total_duration matches what real-time produced (within a tolerance).
        dur = s.total_duration()
        assert dur is not None
        self.assertGreater(dur.total_seconds(), 0.05)
        self.assertLess(dur.total_seconds(), 1.0)


if __name__ == "__main__":
    unittest.main()
