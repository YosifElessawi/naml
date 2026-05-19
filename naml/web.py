"""Minimal HTTP server exposing project state for the dashboard.

Single endpoint:

    GET /api/state    →  {"project": {...}, "current_sprint": {...}, "lanes": [...]}

Stdlib-only (no Flask / FastAPI). The dashboard (Phase 6, issue #8) drives
this with cheap ETag-validated polling — the body is hashed and returned
as a weak ETag; clients echo it back via ``If-None-Match`` to get a 304
when nothing has changed.

The body is the exact payload :func:`naml.project_state.build_hierarchy`
produces — re-use it for ``naml status`` so the CLI and the dashboard
never disagree about what state means.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import logging
import threading
from typing import Any

from . import project_state as project_state_mod


log = logging.getLogger("naml.web")


def _compute_response(
    cfg: Any,
    *,
    if_none_match: str | None,
) -> tuple[int, bytes, dict[str, str]]:
    """Pure function — easy to unit test without a real server.

    Returns ``(status_code, body_bytes, headers)``. On a fresh request or
    a stale ``If-None-Match`` returns 200 with the full body; on a match
    returns 304 with an empty body (and only the ETag header).
    """
    payload = project_state_mod.build_hierarchy(cfg)
    body = json.dumps(payload, indent=2).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()[:16]
    etag = f'W/"{digest}"'

    if if_none_match and if_none_match.strip() == etag:
        return 304, b"", {"ETag": etag}

    return 200, body, {
        "Content-Type": "application/json; charset=utf-8",
        "ETag": etag,
        "Cache-Control": "no-cache",
    }


def _make_handler_class(cfg: Any) -> type[http.server.BaseHTTPRequestHandler]:
    """Bind cfg into a handler class — the server instantiates fresh
    handlers per request, so the config has to be captured in a closure
    rather than passed through __init__."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        # Silence the default per-request stderr noise; we log explicitly.
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
            return

        def do_GET(self) -> None:  # noqa: N802 — stdlib convention
            if self.path != "/api/state":
                self.send_error(404, "not found")
                return

            if_none_match = self.headers.get("If-None-Match")
            try:
                status, body, headers = _compute_response(
                    cfg, if_none_match=if_none_match
                )
            except Exception:  # noqa: BLE001 — defence in depth
                log.exception("/api/state handler failed")
                self.send_error(500, "internal error")
                return

            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            if status != 304:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

    return _Handler


def serve(
    cfg: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 7777,
) -> None:
    """Block on the server. Intended for ``naml serve``. Ctrl-C exits."""
    handler_cls = _make_handler_class(cfg)
    server = http.server.ThreadingHTTPServer((host, port), handler_cls)
    log.info("naml web server listening on http://%s:%d/api/state", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("naml web server interrupted — shutting down")
    finally:
        server.server_close()


def start_in_thread(
    cfg: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[http.server.ThreadingHTTPServer, threading.Thread]:
    """Start the server on a background thread. Returns ``(server, thread)``
    so the caller can ``server.shutdown()`` cleanly. ``port=0`` lets the OS
    pick a free port (useful for tests; read ``server.server_address[1]``).
    """
    handler_cls = _make_handler_class(cfg)
    server = http.server.ThreadingHTTPServer((host, port), handler_cls)
    thread = threading.Thread(
        target=server.serve_forever, daemon=True, name="naml-web"
    )
    thread.start()
    return server, thread
