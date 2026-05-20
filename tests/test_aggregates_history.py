"""Tests for ``naml.aggregates_history``."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from naml import aggregates_history


class ReadHistoryTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(
            aggregates_history.read_history(Path("/no/such/file.jsonl")), []
        )

    def test_round_trip_append_then_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            aggregates_history.append_point(
                path,
                aggregates_history.HistoryPoint(
                    day="2026-05-18", cost_usd=2.5, tokens=100
                ),
            )
            aggregates_history.append_point(
                path,
                aggregates_history.HistoryPoint(
                    day="2026-05-19", cost_usd=1.0, tokens=50
                ),
            )
            points = aggregates_history.read_history(path)
        self.assertEqual([p.day for p in points], ["2026-05-18", "2026-05-19"])
        self.assertEqual(points[0].cost_usd, 2.5)

    def test_same_day_collapses_to_last_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            aggregates_history.append_point(
                path,
                aggregates_history.HistoryPoint(day="2026-05-19", cost_usd=1.0),
            )
            aggregates_history.append_point(
                path,
                aggregates_history.HistoryPoint(day="2026-05-19", cost_usd=4.0),
            )
            points = aggregates_history.read_history(path)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].cost_usd, 4.0)

    def test_write_or_replace_today_rewrites_today(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            aggregates_history.append_point(
                path,
                aggregates_history.HistoryPoint(day="2026-05-18", cost_usd=2.5),
            )
            aggregates_history.write_or_replace_today(
                path,
                aggregates_history.HistoryPoint(day="2026-05-19", cost_usd=3.0),
            )
            aggregates_history.write_or_replace_today(
                path,
                aggregates_history.HistoryPoint(day="2026-05-19", cost_usd=4.0),
            )
            text = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(text), 2)
            parsed_days = sorted(json.loads(line)["day"] for line in text)
            self.assertEqual(parsed_days, ["2026-05-18", "2026-05-19"])
            today_row = next(
                json.loads(line) for line in text if json.loads(line)["day"] == "2026-05-19"
            )
            self.assertEqual(today_row["cost_usd"], 4.0)

    def test_corrupt_lines_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            path.write_text(
                'not json\n{"day": "2026-05-19", "cost_usd": 1.0}\n\n',
                encoding="utf-8",
            )
            points = aggregates_history.read_history(path)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].day, "2026-05-19")

    def test_days_window_truncates_to_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            for day in ("2026-05-15", "2026-05-16", "2026-05-17", "2026-05-18"):
                aggregates_history.append_point(
                    path, aggregates_history.HistoryPoint(day=day)
                )
            points = aggregates_history.read_history(path, days=2)
        self.assertEqual([p.day for p in points], ["2026-05-17", "2026-05-18"])

    def test_build_response_returns_points_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            aggregates_history.append_point(
                path,
                aggregates_history.HistoryPoint(day="2026-05-19", cost_usd=4.21),
            )
            payload = aggregates_history.build_response(path)
        self.assertIn("points", payload)
        self.assertEqual(payload["points"][0]["day"], "2026-05-19")
        self.assertEqual(payload["points"][0]["cost_usd"], 4.21)

    def test_invalid_day_strings_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            path.write_text(
                '{"day": "not-a-date", "cost_usd": 1.0}\n'
                '{"day": "2026-05-19", "cost_usd": 2.0}\n',
                encoding="utf-8",
            )
            points = aggregates_history.read_history(path)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].day, "2026-05-19")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
