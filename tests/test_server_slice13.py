"""Tests for the slice-13 cockpit endpoints: feedback-inbox and aggregates-history.

The Phase-5 ``/api/state`` + ``/healthz`` coverage lives in ``test_server.py``;
this file isolates the slice-13 additions so the orchestrator's tiered
merger can compose with slice-11's eventual ``/events`` route without
trampling unrelated tests.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase

from naml import aggregates_history as ah_mod
from naml import server as server_mod


class _StubCfg:
    """Minimal duck-typed NamlConfig — only the attrs the handlers touch."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.sprints_path = repo_root / ".naml" / "sprints"


class FeedbackInboxRouteTests(AioHTTPTestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self._root = Path(self._td.name).resolve()
        (self._root / "docs" / "feedback").mkdir(parents=True)
        (self._root / ".naml" / "sprints").mkdir(parents=True)
        (self._root / "docs" / "feedback" / "inbox.md").write_text(
            "## Unfiled\n\n"
            "- first item\n"
            "- second item (from postmortem)\n"
            "- (2026-05-19) third item\n"
            "\n## Filed\n\n"
            "- ignored\n",
            encoding="utf-8",
        )
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()
        self._td.cleanup()

    async def get_application(self):  # type: ignore[override]
        return server_mod.build_app(
            _StubCfg(self._root), web_dist=self._root / "web" / "dist"
        )

    async def test_returns_unfiled_count_and_recent_bullets(self) -> None:
        resp = await self.client.get("/api/feedback-inbox")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["unfiledCount"], 3)
        self.assertLessEqual(len(body["bullets"]), 3)
        text_set = {b["text"] for b in body["bullets"]}
        self.assertIn("third item", text_set)

    async def test_missing_inbox_returns_zero_count(self) -> None:
        (self._root / "docs" / "feedback" / "inbox.md").unlink()
        resp = await self.client.get("/api/feedback-inbox")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["unfiledCount"], 0)
        self.assertEqual(body["bullets"], [])


class AggregatesHistoryRouteTests(AioHTTPTestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self._root = Path(self._td.name).resolve()
        (self._root / ".naml" / "sprints").mkdir(parents=True)
        state_dir = self._root / "state"
        state_dir.mkdir(parents=True)
        path = state_dir / "aggregates-history.jsonl"
        for day, cost in (
            ("2026-05-15", 1.0),
            ("2026-05-16", 2.0),
            ("2026-05-17", 3.0),
        ):
            ah_mod.append_point(
                path, ah_mod.HistoryPoint(day=day, cost_usd=cost, tokens=100)
            )
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()
        self._td.cleanup()

    async def get_application(self):  # type: ignore[override]
        return server_mod.build_app(
            _StubCfg(self._root), web_dist=self._root / "web" / "dist"
        )

    async def test_returns_history_points(self) -> None:
        resp = await self.client.get("/api/aggregates-history")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(len(body["points"]), 3)
        self.assertEqual(body["points"][0]["day"], "2026-05-15")
        self.assertEqual(body["points"][-1]["cost_usd"], 3.0)

    async def test_days_query_param_limits_window(self) -> None:
        resp = await self.client.get("/api/aggregates-history?days=2")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(len(body["points"]), 2)
        self.assertEqual(body["points"][0]["day"], "2026-05-16")

    async def test_invalid_days_returns_400(self) -> None:
        resp = await self.client.get("/api/aggregates-history?days=oops")
        self.assertEqual(resp.status, 400)

    async def test_missing_file_returns_empty_points(self) -> None:
        (self._root / "state" / "aggregates-history.jsonl").unlink()
        resp = await self.client.get("/api/aggregates-history")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["points"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
