"""Tests for ``naml.feedback_inbox``."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from naml import feedback_inbox


SAMPLE_INBOX = """\
# Inbox

## Unfiled

- Fix the cockpit sync dot animation when reduced motion is set
- (2026-05-19) Tier-1 hit % drops when slice-4 retries (from postmortem)
- Investigate cold-start replay slowness

## Filed

- Already-handled bullet that should be ignored
"""


class ParseInboxTests(unittest.TestCase):
    def test_unfiled_bullets_are_captured(self) -> None:
        bullets = feedback_inbox.parse_inbox(SAMPLE_INBOX)
        self.assertEqual(len(bullets), 3)
        self.assertEqual(bullets[0].source, "Unfiled")
        self.assertIn("cockpit sync dot", bullets[0].text)

    def test_filed_bullets_are_skipped(self) -> None:
        bullets = feedback_inbox.parse_inbox(SAMPLE_INBOX)
        for b in bullets:
            self.assertNotIn("Already-handled", b.text)

    def test_from_annotation_overrides_section_as_source(self) -> None:
        bullets = feedback_inbox.parse_inbox(SAMPLE_INBOX)
        annotated = next(b for b in bullets if "Tier-1" in b.text)
        self.assertEqual(annotated.source, "postmortem")
        self.assertIn("(from", "(from x)")  # sanity

    def test_iso_date_is_extracted(self) -> None:
        bullets = feedback_inbox.parse_inbox(SAMPLE_INBOX)
        dated = next(b for b in bullets if b.added_at)
        self.assertEqual(dated.added_at, "2026-05-19")

    def test_ids_are_stable_across_calls(self) -> None:
        a = feedback_inbox.parse_inbox(SAMPLE_INBOX)
        b = feedback_inbox.parse_inbox(SAMPLE_INBOX)
        self.assertEqual([x.id for x in a], [x.id for x in b])

    def test_missing_file_returns_empty(self) -> None:
        bullets = feedback_inbox.load_inbox(Path("/no/such/path/inbox.md"))
        self.assertEqual(bullets, [])

    def test_build_response_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "inbox.md"
            path.write_text(SAMPLE_INBOX, encoding="utf-8")
            payload = feedback_inbox.build_response(path)
        self.assertEqual(payload["unfiledCount"], 3)
        self.assertLessEqual(len(payload["bullets"]), 3)
        first = payload["bullets"][0]
        self.assertIn("id", first)
        self.assertIn("text", first)
        self.assertIn("source", first)
        self.assertIn("addedAt", first)

    def test_dated_bullets_sort_to_the_front(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "inbox.md"
            path.write_text(SAMPLE_INBOX, encoding="utf-8")
            payload = feedback_inbox.build_response(path, recent=3)
        # The 2026-05-19 bullet has a date; the other two don't.
        self.assertEqual(payload["bullets"][0]["addedAt"], "2026-05-19")

    def test_empty_text_returns_empty(self) -> None:
        self.assertEqual(feedback_inbox.parse_inbox(""), [])

    def test_lines_without_bullets_are_ignored(self) -> None:
        bullets = feedback_inbox.parse_inbox(
            "Some narrative paragraph.\n\nAnother sentence.\n"
        )
        self.assertEqual(bullets, [])


if __name__ == "__main__":  # pragma: no cover — convenience
    unittest.main()
