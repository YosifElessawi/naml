"""Tests for ``naml.web`` — /api/state endpoint + ETag handling."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from naml import project_state as ps
from naml import state as state_mod
from naml.web import _compute_response, start_in_thread


class _StubCfg:
    """Minimal duck-typed NamlConfig for web tests."""

    def __init__(self, repo_root: Path, sprints_path: Path) -> None:
        self.repo_root = repo_root
        self.sprints_path = sprints_path


class ComputeResponseTests(unittest.TestCase):
    def _make_cfg(self, td: str) -> _StubCfg:
        root = Path(td).resolve()
        sprints = root / ".naml" / "sprints"
        sprints.mkdir(parents=True)
        return _StubCfg(repo_root=root, sprints_path=sprints)

    def test_first_request_returns_200_with_etag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = self._make_cfg(td)
            status, body, headers = _compute_response(cfg, if_none_match=None)

            self.assertEqual(status, 200)
            self.assertIn("ETag", headers)
            self.assertTrue(headers["ETag"].startswith('W/"'))
            payload = json.loads(body)
            self.assertEqual(payload["project"]["state"], "idle")

    def test_matching_etag_returns_304_empty_body(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = self._make_cfg(td)
            _, _, headers = _compute_response(cfg, if_none_match=None)
            etag = headers["ETag"]

            status, body, h2 = _compute_response(cfg, if_none_match=etag)
            self.assertEqual(status, 304)
            self.assertEqual(body, b"")
            self.assertEqual(h2["ETag"], etag)

    def test_stale_etag_returns_200_with_new_etag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = self._make_cfg(td)
            _, _, headers = _compute_response(cfg, if_none_match=None)
            stale = headers["ETag"]

            ps.on_sprint_start(Path(td).resolve() / ".naml", "sprint-A")

            status, body, h2 = _compute_response(cfg, if_none_match=stale)
            self.assertEqual(status, 200)
            self.assertNotEqual(h2["ETag"], stale)
            payload = json.loads(body)
            self.assertEqual(payload["project"]["state"], "active")
            self.assertEqual(payload["project"]["current_sprint"], "sprint-A")

    def test_payload_includes_loaded_slice_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = self._make_cfg(td)
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

            status, body, _ = _compute_response(cfg, if_none_match=None)
            self.assertEqual(status, 200)
            payload = json.loads(body)
            self.assertIsNotNone(payload["current_sprint"])
            self.assertEqual(
                payload["current_sprint"]["slices"][0]["slice_id"], "slice-1"
            )


class IntegrationServerTests(unittest.TestCase):
    """Spin up the actual HTTPServer on a free port and hit it."""

    def test_full_http_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            sprints = root / ".naml" / "sprints"
            sprints.mkdir(parents=True)
            cfg = _StubCfg(repo_root=root, sprints_path=sprints)

            server, thread = start_in_thread(cfg, host="127.0.0.1", port=0)
            try:
                port = server.server_address[1]
                url = f"http://127.0.0.1:{port}/api/state"

                with urllib.request.urlopen(url, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    etag = resp.headers["ETag"]
                    body = json.loads(resp.read())
                self.assertIsNotNone(etag)
                self.assertEqual(body["project"]["state"], "idle")

                # Second request with the ETag should 304.
                req = urllib.request.Request(url, headers={"If-None-Match": etag})
                # urllib raises HTTPError on 3xx — catch it explicitly.
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp2:
                        # Some Python versions follow redirects; 304 should
                        # NOT have a body.
                        self.assertEqual(resp2.status, 304)
                except urllib.error.HTTPError as err:
                    self.assertEqual(err.code, 304)

                # 404 path
                bad_url = f"http://127.0.0.1:{port}/api/nope"
                try:
                    urllib.request.urlopen(bad_url, timeout=5)
                    self.fail("expected HTTPError on /api/nope")
                except urllib.error.HTTPError as err:
                    self.assertEqual(err.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
