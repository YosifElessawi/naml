"""Tests for naml.claude helpers that don't require spawning subprocesses."""

from __future__ import annotations

import unittest

from naml.claude import parse_verdict


class ParseVerdictTests(unittest.TestCase):
    def test_lgtm(self) -> None:
        text = "Reviewed the diff, looks good.\n\nVERDICT: LGTM\n"
        self.assertEqual(parse_verdict(text), "LGTM")

    def test_request_changes(self) -> None:
        text = "Missing acceptance criterion 2.\n\nVERDICT: REQUEST_CHANGES"
        self.assertEqual(parse_verdict(text), "REQUEST_CHANGES")

    def test_abandon(self) -> None:
        text = "Architecture is wrong; redo.\n\nVERDICT: ABANDON\n"
        self.assertEqual(parse_verdict(text), "ABANDON")

    def test_unknown_verdict(self) -> None:
        text = "Something weird, no verdict\n"
        self.assertEqual(parse_verdict(text), "UNKNOWN")

    def test_invalid_verdict_value(self) -> None:
        text = "VERDICT: MAYBE\n"
        self.assertEqual(parse_verdict(text), "UNKNOWN")

    def test_picks_last_verdict_when_multiple_present(self) -> None:
        text = (
            "VERDICT: REQUEST_CHANGES\n"
            "\n"
            "On further thought:\n"
            "\n"
            "VERDICT: LGTM\n"
        )
        self.assertEqual(parse_verdict(text), "LGTM")

    def test_case_insensitive(self) -> None:
        text = "verdict: lgtm\n"
        self.assertEqual(parse_verdict(text), "LGTM")


if __name__ == "__main__":
    unittest.main()
