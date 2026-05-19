"""Tests for naml.states + naml.run aggregation."""

from __future__ import annotations

import unittest

from naml import states
from naml.run import _aggregate_state


class StateConstantsTests(unittest.TestCase):
    def test_retry_caps_match_design_doc(self) -> None:
        self.assertEqual(states.retry_cap(states.WORK), 2)
        self.assertEqual(states.retry_cap(states.PR), 3)
        self.assertEqual(states.retry_cap(states.REVIEW), 3)

    def test_retry_cap_unknown_state_is_zero(self) -> None:
        self.assertEqual(states.retry_cap("nonsense"), 0)

    def test_done_and_failed_partitions_are_disjoint(self) -> None:
        self.assertEqual(states.LANE_DONE_STATES & states.LANE_FAILED_STATES, set())

    def test_review_passed_counts_as_done(self) -> None:
        self.assertIn(states.REVIEW_PASSED, states.LANE_DONE_STATES)

    def test_needs_human_review_counts_as_failed_for_scheduler(self) -> None:
        # The scheduler treats it as a failure so dependents are blocked,
        # even though the dashboard surfaces it differently.
        self.assertIn(states.NEEDS_HUMAN_REVIEW, states.LANE_FAILED_STATES)


class AggregateStateTests(unittest.TestCase):
    def test_all_review_passed_yields_awaiting_signoff_when_stop_review(self) -> None:
        per_slice = {"slice-1": states.REVIEW_PASSED, "slice-2": states.REVIEW_PASSED}
        self.assertEqual(_aggregate_state(per_slice, "review"),
                         states.SPRINT_AWAITING_SIGNOFF)

    def test_all_pr_yields_awaiting_signoff_when_stop_pr(self) -> None:
        per_slice = {"slice-1": states.PR, "slice-2": states.PR}
        self.assertEqual(_aggregate_state(per_slice, "pr"),
                         states.SPRINT_AWAITING_SIGNOFF)

    def test_pr_state_below_target_review_still_executing(self) -> None:
        # Stop-after=review with a slice stuck at PR (not yet reviewed) →
        # the run shouldn't return awaiting_signoff.
        per_slice = {"slice-1": states.REVIEW_PASSED, "slice-2": states.PR}
        self.assertEqual(_aggregate_state(per_slice, "review"),
                         states.SPRINT_EXECUTING)

    def test_one_failure_yields_partial(self) -> None:
        per_slice = {"slice-1": states.REVIEW_PASSED, "slice-2": states.FAILED}
        self.assertEqual(_aggregate_state(per_slice, "review"),
                         states.SPRINT_PARTIAL_FAILURE)

    def test_one_needs_human_review_yields_partial(self) -> None:
        per_slice = {
            "slice-1": states.REVIEW_PASSED,
            "slice-2": states.NEEDS_HUMAN_REVIEW,
        }
        self.assertEqual(_aggregate_state(per_slice, "review"),
                         states.SPRINT_PARTIAL_FAILURE)

    def test_all_failed_yields_failed(self) -> None:
        per_slice = {"slice-1": states.FAILED, "slice-2": states.BLOCKED_UPSTREAM}
        self.assertEqual(_aggregate_state(per_slice, "review"),
                         states.SPRINT_FAILED)

    def test_empty_input_treated_as_failed(self) -> None:
        self.assertEqual(_aggregate_state({}, "review"), states.SPRINT_FAILED)

    def test_merge_target_requires_merged_state(self) -> None:
        # If stop_after=merge but slices are only at review_passed, it's
        # still executing — the merger hasn't run yet.
        per_slice = {"slice-1": states.REVIEW_PASSED}
        self.assertEqual(_aggregate_state(per_slice, "merge"),
                         states.SPRINT_EXECUTING)
        per_slice = {"slice-1": states.MERGED}
        self.assertEqual(_aggregate_state(per_slice, "merge"),
                         states.SPRINT_COMPLETE)


if __name__ == "__main__":
    unittest.main()
