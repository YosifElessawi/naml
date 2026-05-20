"""Slice + sprint state-machine constants.

Centralised so the lane worker, scheduler, state persistence, and reports
all use the same string labels. The constants match the design doc
(``docs/DESIGN-V2.md`` § 6).
"""

from __future__ import annotations

from typing import Final, Literal


# --- per-slice state machine ---------------------------------------------

PENDING: Final = "pending"
SETUP: Final = "setup"
WORK: Final = "work"
PR: Final = "pr"
REVIEW: Final = "review"
MERGING: Final = "merging"            # Phase 4 — slice is in the tiered merge pipeline
MERGED: Final = "merged"

# Safe-intervention pause. User-triggered via the cockpit's HOLD button;
# the lane finishes its current Claude turn, releases its worktree lock,
# and idles until the user presses RESUME (back to ``work``) or escalates
# to FAILED / ABANDONED. See slice-14 in sprint 2026-05-19-cockpit-v2.
HELD: Final = "held"

# Terminals (failure / pause).
FAILED: Final = "failed"
NEEDS_INFO: Final = "needs_info"
NEEDS_HUMAN_REVIEW: Final = "needs_human_review"
ABANDONED: Final = "abandoned"
BLOCKED_UPSTREAM: Final = "blocked_upstream"
MERGE_BLOCKED: Final = "merge_blocked"  # Phase 4 — Tier 4 escalation

# Phase 3 milestone — the slice has cleared auto-review but the sprint has
# not been merged yet. The scheduler treats this as "done" so it can
# release dependents; the dashboard surfaces it as "awaiting human signoff".
REVIEW_PASSED: Final = "review_passed"


SliceState = Literal[
    "pending", "setup", "work", "pr", "review", "review_passed",
    "merging", "merged", "held",
    "failed", "needs_info", "needs_human_review", "abandoned",
    "blocked_upstream", "merge_blocked",
]


# States in which the slice is "safe to drop into a terminal" — naml is
# not actively driving a Claude session. The cockpit's open-in-terminal
# action is gated by this set. Mirrored on the browser side in
# ``web/src/components/SliceDrawer/format.ts``.
TERMINAL_UNLOCKED_STATES: Final[frozenset[str]] = frozenset({
    HELD, REVIEW, REVIEW_PASSED, MERGED,
    FAILED, NEEDS_HUMAN_REVIEW, ABANDONED, BLOCKED_UPSTREAM,
})


# States from which a HOLD intervention is meaningful. The lane only
# checks the sentinel inside the work/gate-fix/review-fix loops; ``pr``
# and downstream states never enter the spin loop, so writing a
# sentinel there would orphan the file. Per the slice-14 spec:
#
#   > pr state is NOT held-able. Once the PR is open, the implementer
#   > session is already at rest; user can open terminal directly.
HOLDABLE_STATES: Final[frozenset[str]] = frozenset({SETUP, WORK})

# States that count as "the lane is done with this slice, scheduler may
# release dependents". A slice in needs_human_review or failed does NOT
# qualify — its dependents are blocked.
LANE_DONE_STATES: Final[frozenset[str]] = frozenset({
    PR, REVIEW_PASSED, MERGED,
})

LANE_FAILED_STATES: Final[frozenset[str]] = frozenset({
    FAILED, NEEDS_HUMAN_REVIEW, ABANDONED, NEEDS_INFO, BLOCKED_UPSTREAM,
    MERGE_BLOCKED,
})


# --- per-state retry caps -------------------------------------------------
#
# Design-doc rule: retry caps are per state, not per slice. Within a state
# the lane gets N attempts before the slice is escalated.

RETRY_CAPS: Final[dict[str, int]] = {
    WORK: 2,        # gate-fix loops
    PR: 3,          # push/PR-open transient failures
    REVIEW: 3,      # review → request_changes → fix → re-review loops
}


def retry_cap(state: str) -> int:
    """Return the configured retry cap for a state, default 0."""
    return RETRY_CAPS.get(state, 0)


# --- sprint state machine ------------------------------------------------

SPRINT_PACKAGE_RECEIVED: Final = "package_received"
SPRINT_PLANNING: Final = "planning"
SPRINT_PUBLISHING_ISSUES: Final = "publishing_issues"
SPRINT_EXECUTING: Final = "executing"
SPRINT_AWAITING_SIGNOFF: Final = "awaiting_signoff"
SPRINT_MERGING: Final = "merging"
SPRINT_COMPLETE: Final = "complete"
SPRINT_PARTIAL_FAILURE: Final = "partial_failure"
SPRINT_FAILED: Final = "failed"
SPRINT_MERGE_BLOCKED: Final = "merge_blocked"


SprintState = Literal[
    "package_received", "planning", "publishing_issues", "executing",
    "awaiting_signoff", "merging", "complete", "partial_failure", "failed",
    "merge_blocked",
]
