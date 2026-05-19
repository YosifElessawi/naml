"""Tests for ``naml.server`` — the aiohttp cockpit server.

Covers the routes the cockpit (and ``naml status``) depend on:

- ``/healthz`` liveness probe
- ``/api/state`` ETag-validated payload (must match the Phase-5 contract so
  ``naml status`` consumers keep working)
- ``/state`` placeholder (real SSE lands in slice-11)
- ``/`` serves ``web/dist/index.html`` when built; informative 404 otherwise

Uses ``aiohttp.test_utils`` to drive the real app on a free port without
spinning up ``serve()`` and its event-loop blocking.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase

from naml import project_state as ps
from naml import server as server_mod
from naml import state as state_mod


class _StubCfg:
    """Minimal duck-typed NamlConfig — only the attrs the server touches."""

    def __init__(self, repo_root: Path, sprints_path: Path) -> None:
        self.repo_root = repo_root
        self.sprints_path = sprints_path


def _make_cfg(td: str) -> _StubCfg:
    root = Path(td).resolve()
    sprints = root / ".naml" / "sprints"
    sprints.mkdir(parents=True)
    return _StubCfg(repo_root=root, sprints_path=sprints)


class StateResponseUnitTests(unittest.TestCase):
    """Exercise ``_state_response`` directly — no server, no event loop."""

    def test_first_request_returns_200_with_etag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_cfg(td)
            resp = server_mod._state_response(cfg, if_none_match=None)

            self.assertEqual(resp.status, 200)
            self.assertIn("ETag", resp.headers)
            self.assertTrue(resp.headers["ETag"].startswith('W/"'))
            payload = json.loads(resp.body)
            self.assertEqual(payload["project"]["state"], "idle")

    def test_matching_etag_returns_304_empty_body(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_cfg(td)
            first = server_mod._state_response(cfg, if_none_match=None)
            etag = first.headers["ETag"]

            second = server_mod._state_response(cfg, if_none_match=etag)
            self.assertEqual(second.status, 304)
            self.assertEqual(second.body, b"")
            self.assertEqual(second.headers["ETag"], etag)

    def test_stale_etag_returns_200_with_new_etag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_cfg(td)
            first = server_mod._state_response(cfg, if_none_match=None)
            stale = first.headers["ETag"]

            ps.on_sprint_start(Path(td).resolve() / ".naml", "sprint-A")

            second = server_mod._state_response(cfg, if_none_match=stale)
            self.assertEqual(second.status, 200)
            self.assertNotEqual(second.headers["ETag"], stale)
            payload = json.loads(second.body)
            self.assertEqual(payload["project"]["state"], "active")
            self.assertEqual(payload["project"]["current_sprint"], "sprint-A")

    def test_payload_includes_loaded_slice_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_cfg(td)
            root = Path(td).resolve()

            ps.on_sprint_start(root / ".naml", "sprint-A")

            sprint_root = cfg.sprints_path / "sprint-A"
            (sprint_root / "state").mkdir(parents=True)
            sstate = state_mod.SprintState(
                sprint_id="sprint-A",
                state="executing",
                slices={"slice-1": "pr"},
            )
            sstate.record_transition(state="executing", detail="started")
            state_mod.save_sprint_state(sprint_root, sstate)

            slice_status = state_mod.SliceStatus(slice_id="slice-1", state="pr")
            slice_status.record_transition(state="pr", detail="opened")
            state_mod.save_slice_status(sprint_root, slice_status)

            resp = server_mod._state_response(cfg, if_none_match=None)
            self.assertEqual(resp.status, 200)
            payload = json.loads(resp.body)
            self.assertIsNotNone(payload["current_sprint"])
            self.assertEqual(
                payload["current_sprint"]["slices"][0]["slice_id"], "slice-1"
            )


class _ServerIntegrationBase(AioHTTPTestCase):
    """Shared scaffolding for end-to-end HTTP tests."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self._root = Path(self._td.name).resolve()
        (self._root / ".naml" / "sprints").mkdir(parents=True)
        self._dist = self._root / "web" / "dist"
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()
        self._td.cleanup()

    async def get_application(self):  # type: ignore[override]
        cfg = _StubCfg(
            repo_root=self._root,
            sprints_path=self._root / ".naml" / "sprints",
        )
        return server_mod.build_app(cfg, web_dist=self._dist)


class HealthzTests(_ServerIntegrationBase):
    async def test_healthz_returns_ok(self) -> None:
        resp = await self.client.get("/healthz")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body, {"status": "ok"})


class ApiStateTests(_ServerIntegrationBase):
    async def test_full_etag_roundtrip(self) -> None:
        resp = await self.client.get("/api/state")
        self.assertEqual(resp.status, 200)
        etag = resp.headers["ETag"]
        self.assertTrue(etag.startswith('W/"'))
        payload = await resp.json()
        self.assertEqual(payload["project"]["state"], "idle")

        resp2 = await self.client.get(
            "/api/state", headers={"If-None-Match": etag}
        )
        self.assertEqual(resp2.status, 304)
        # 304 must not carry a body.
        self.assertEqual(await resp2.read(), b"")


class StatePlaceholderTests(_ServerIntegrationBase):
    async def test_state_returns_empty_object(self) -> None:
        resp = await self.client.get("/state")
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.json(), {})


class StaticServingTests(_ServerIntegrationBase):
    async def test_index_404s_until_built(self) -> None:
        resp = await self.client.get("/")
        self.assertEqual(resp.status, 404)
        # Hint should mention how to fix it — failing helpfully matters here.
        body = await resp.text()
        self.assertIn("pnpm", body)
        self.assertIn("web/dist", body)


class BuiltIndexTests(AioHTTPTestCase):
    """Same as above but writes a fake bundle first to prove `/` serves it."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self._root = Path(self._td.name).resolve()
        (self._root / ".naml" / "sprints").mkdir(parents=True)
        self._dist = self._root / "web" / "dist"
        self._dist.mkdir(parents=True)
        (self._dist / "index.html").write_text(
            "<!doctype html><title>built</title>", encoding="utf-8"
        )
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()
        self._td.cleanup()

    async def get_application(self):  # type: ignore[override]
        cfg = _StubCfg(
            repo_root=self._root,
            sprints_path=self._root / ".naml" / "sprints",
        )
        return server_mod.build_app(cfg, web_dist=self._dist)

    async def test_built_index_is_served(self) -> None:
        resp = await self.client.get("/")
        self.assertEqual(resp.status, 200)
        body = await resp.text()
        self.assertIn("built", body)


if __name__ == "__main__":
    unittest.main()
