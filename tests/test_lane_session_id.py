"""Tests for ``naml.lane._fresh_session_id``.

When a lane claims a slice it MUST mint a new session_id, even if the
persisted status already carries one from a prior ``naml run``. Reusing
a session_id makes Claude reject the spawn:

    Error: Session ID <uuid> is already in use.

… which kills the implementer subprocess with exit code 1 in under a
second and was the primary smoking gun in the cockpit-v2 re-run.
"""

from __future__ import annotations

import unittest
import uuid

from naml import state as state_mod
from naml.lane import _fresh_session_id


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


class FreshSessionIdTests(unittest.TestCase):
    def test_replaces_existing_session_id(self) -> None:
        old = "8f5875a1-371a-4907-986b-795521388139"  # the real bug-report ID
        status = state_mod.SliceStatus(slice_id="slice-1", session_id=old)
        _fresh_session_id(status)
        self.assertTrue(_is_uuid(status.session_id))
        self.assertNotEqual(status.session_id, old)

    def test_assigns_when_empty(self) -> None:
        status = state_mod.SliceStatus(slice_id="slice-1", session_id="")
        _fresh_session_id(status)
        self.assertTrue(_is_uuid(status.session_id))

    def test_each_call_returns_a_distinct_uuid(self) -> None:
        # Two consecutive lane claims must never re-use an ID, even within
        # the same Python process.
        status = state_mod.SliceStatus(slice_id="slice-1", session_id="seed")
        _fresh_session_id(status)
        first = status.session_id
        _fresh_session_id(status)
        second = status.session_id
        self.assertNotEqual(first, second)

    def test_does_not_clobber_other_fields(self) -> None:
        status = state_mod.SliceStatus(
            slice_id="slice-1",
            session_id="old-id",
            branch="naml/sprint/slice-1",
            worktree="/tmp/wt",
            state="setup",
            attempts={"work": 1},
            last_error="something",
        )
        _fresh_session_id(status)
        # Only session_id should change.
        self.assertNotEqual(status.session_id, "old-id")
        self.assertEqual(status.branch, "naml/sprint/slice-1")
        self.assertEqual(status.worktree, "/tmp/wt")
        self.assertEqual(status.state, "setup")
        self.assertEqual(status.attempts, {"work": 1})
        self.assertEqual(status.last_error, "something")


if __name__ == "__main__":
    unittest.main()
