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
from naml.aggregator import Aggregator


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


class AggregatesEndpointTests(AioHTTPTestCase):
    """Cover ``GET /aggregates`` + ``POST /aggregates/reset``.

    We seed the project with one JSONL line so cold-start replay has
    something to find, then verify both endpoints respond with the
    expected shape and behaviour.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self._root = Path(self._td.name).resolve()
        sprints = self._root / ".naml" / "sprints"
        sprints.mkdir(parents=True)
        # One JSONL line so cold-start observes a non-empty replay.
        state = sprints / "sprint-A" / "state"
        state.mkdir(parents=True)
        (state / "slice-1.tokens.jsonl").write_text(
            json.dumps({
                "t": "2026-05-19T14:23:14.812+00:00",
                "slice": "slice-1",
                "session": "sess",
                "turn": 1,
                "tokens_in": 100,
                "tokens_out": 50,
                "cache_read": 0,
                "cache_write": 0,
                "cost_usd": 0.10,
                "ctx_pct": 5,
            }) + "\n",
            encoding="utf-8",
        )
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
        # build_app does cold-start replay by default.
        return server_mod.build_app(cfg, web_dist=self._dist)

    async def test_get_aggregates_returns_spec_shape(self) -> None:
        resp = await self.client.get("/aggregates")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(
            set(body.keys()), {"per_slice", "per_sprint", "per_project"}
        )
        self.assertIn("slice-1", body["per_slice"])
        self.assertIn("sprint-A", body["per_sprint"])
        # The headline cost timeline keys must all be present.
        for win in ("today", "this_week", "last_30d", "lifetime"):
            self.assertIn(win, body["per_project"])

    async def test_reset_recomputes_correctly(self) -> None:
        # Mutate the aggregator in place — simulating drift — then reset.
        agg: Aggregator = self.app[server_mod.APP_KEY_AGGREGATOR]
        agg.reset()
        # Reset alone should have zeroed it out.
        self.assertEqual(agg.per_slice, {})

        resp = await self.client.post("/aggregates/reset")
        self.assertEqual(resp.status, 200)
        payload = await resp.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["events_replayed"], 1)

        # Aggregator must once again know about the seeded event.
        resp2 = await self.client.get("/aggregates")
        body = await resp2.json()
        self.assertIn("slice-1", body["per_slice"])
        self.assertAlmostEqual(
            body["per_slice"]["slice-1"]["cost_usd"], 0.10, places=6,
        )


class IntervenePostTests(_ServerIntegrationBase):
    """POST ``/intervene/{slice_id}`` is the drawer's intervention surface."""

    async def test_hold_returns_200_accepted_stub(self) -> None:
        resp = await self.client.post("/intervene/slice-4?action=hold")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["status"], "accepted")
        self.assertEqual(body["action"], "hold")
        self.assertEqual(body["slice_id"], "slice-4")

    async def test_fail_and_skip_return_200(self) -> None:
        for action in ("fail", "skip"):
            resp = await self.client.post(f"/intervene/slice-4?action={action}")
            self.assertEqual(resp.status, 200, action)
            body = await resp.json()
            self.assertEqual(body["action"], action)

    async def test_unknown_action_returns_400(self) -> None:
        resp = await self.client.post("/intervene/slice-4?action=detonate")
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertIn("error", body)
        self.assertIn("hold", body["valid"])

    async def test_missing_action_returns_400(self) -> None:
        resp = await self.client.post("/intervene/slice-4")
        self.assertEqual(resp.status, 400)

    async def test_open_terminal_is_handled(self) -> None:
        """Verifies the route exists and answers without actually spawning
        Terminal in CI. We monkey-patch ``_spawn_terminal`` so the test is
        deterministic on macOS too."""
        original = server_mod._spawn_terminal
        server_mod._spawn_terminal = lambda *_args, **_kw: False  # type: ignore[assignment]
        try:
            resp = await self.client.post("/intervene/slice-4?action=open-terminal")
            self.assertIn(resp.status, (200, 202))
            body = await resp.json()
            self.assertEqual(body["action"], "open-terminal")
            self.assertIn("launched", body)
            self.assertFalse(body["launched"])
        finally:
            server_mod._spawn_terminal = original  # type: ignore[assignment]

    async def test_open_terminal_rejects_injection_session_id(self) -> None:
        """Crafted session_ids must be rejected at the boundary — they
        cannot reach ``_spawn_terminal``. If they ever did, AppleScript's
        ``do script`` would happily execute shell metacharacters."""
        bad_ids = (
            'abc"; do shell script "rm -rf ~"; #',  # AppleScript breakout
            "abc; rm -rf ~",                         # shell metacharacters
            "abc def",                               # whitespace
            "abc$(date)",                            # command substitution
            "abc`whoami`",                           # backtick subst
            "../../etc/passwd",                     # path traversal
            "abc\nrm",                               # newline injection
            "g" * 64,                                # non-hex chars
            "",                                      # empty (after %-decode)
        )
        spawn_called = {"count": 0}

        def _trap(*_args: object, **_kw: object) -> bool:
            spawn_called["count"] += 1
            return False

        original = server_mod._spawn_terminal
        server_mod._spawn_terminal = _trap  # type: ignore[assignment]
        try:
            for bad in bad_ids:
                from urllib.parse import quote

                resp = await self.client.post(
                    f"/intervene/slice-4?action=open-terminal"
                    f"&session_id={quote(bad)}"
                )
                self.assertEqual(
                    resp.status,
                    400,
                    f"expected 400 for session_id={bad!r}, got {resp.status}",
                )
                body = await resp.json()
                self.assertEqual(body["error"], "invalid session_id", body)
            # Crucially: not one of those bad inputs was allowed to reach
            # the spawner.
            self.assertEqual(spawn_called["count"], 0)
        finally:
            server_mod._spawn_terminal = original  # type: ignore[assignment]

    async def test_open_terminal_accepts_uuid_session_id(self) -> None:
        """A well-formed (UUID-ish) session_id passes the boundary check."""
        original = server_mod._spawn_terminal
        server_mod._spawn_terminal = lambda *_args, **_kw: False  # type: ignore[assignment]
        try:
            resp = await self.client.post(
                "/intervene/slice-4?action=open-terminal"
                "&session_id=9b0c4a82-f3d8-4ef1-90ab-1234567890ab"
            )
            self.assertIn(resp.status, (200, 202))
            body = await resp.json()
            self.assertEqual(body["action"], "open-terminal")
        finally:
            server_mod._spawn_terminal = original  # type: ignore[assignment]


class SpawnTerminalDefenceTests(unittest.TestCase):
    """Direct unit tests of ``_spawn_terminal`` — the second line of
    defence against session-id injection (the handler is the first)."""

    def test_rejects_injection_session_id_even_if_reached(self) -> None:
        """If the handler validator is bypassed in some future refactor,
        ``_spawn_terminal`` itself still won't run a hostile id."""
        # We don't need to monkey-patch subprocess here — the function
        # short-circuits to False before reaching Popen for any non-UUID
        # input.
        with tempfile.TemporaryDirectory() as td:
            worktree = Path(td)
            self.assertFalse(
                server_mod._spawn_terminal(
                    worktree, 'abc"; do shell script "rm -rf ~"; #'
                )
            )
            self.assertFalse(
                server_mod._spawn_terminal(worktree, "abc; rm -rf ~")
            )
            self.assertFalse(server_mod._spawn_terminal(worktree, "abc`id`"))


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
