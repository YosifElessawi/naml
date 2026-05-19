"""Async HTTP server for the V2 cockpit.

Replaces the Phase-5 stopgap ``naml/web.py`` (stdlib ``http.server``). The
cockpit needs to hold long-lived SSE connections (slice-11), so the server
is built on aiohttp with a single asyncio event loop.

Routes:

- ``GET /healthz``         → ``{"status": "ok"}`` (liveness)
- ``GET /api/state``       → ``naml.project_state.build_hierarchy(cfg)`` with
                             weak ETag + ``If-None-Match`` 304 short-circuit.
- ``GET /state``           → ``{}`` placeholder. Becomes the SSE channel in
                             slice-11.
- ``GET /aggregates``      → ``Aggregator.snapshot()`` — token/cost rollups.
- ``POST /aggregates/reset`` → recompute aggregates from disk (Settings →
                             Advanced → Reset Aggregates).
- ``GET /``                → served from ``web/dist/`` when the bundle exists;
                             404 otherwise (Vite owns development).

The aggregator is owned by the ``Application`` instance — one per server.
Cold-start replay runs in :func:`build_app` so the snapshot is warm before
the first request lands.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from . import project_state as project_state_mod
from .aggregator import Aggregator
from .watcher import replay_all


log = logging.getLogger("naml.server")


# aiohttp app[key] entries — typed AppKey instances dodge aiohttp 3.10's
# NotAppKeyWarning and give callers proper static-type hints.
APP_KEY_AGGREGATOR: web.AppKey[Aggregator] = web.AppKey(
    "naml_aggregator", Aggregator
)
APP_KEY_SPRINTS_ROOT: web.AppKey[Path] = web.AppKey(
    "naml_sprints_root", Path
)


def _state_response(cfg: Any, if_none_match: str | None) -> web.Response:
    """Build the ``/api/state`` response. Pure-ish (just reads disk via cfg)
    so unit tests can exercise it without a running server."""
    payload = project_state_mod.build_hierarchy(cfg)
    body = json.dumps(payload, indent=2).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()[:16]
    etag = f'W/"{digest}"'

    if if_none_match and if_none_match.strip() == etag:
        return web.Response(status=304, body=b"", headers={"ETag": etag})

    return web.Response(
        status=200,
        body=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "ETag": etag,
            "Cache-Control": "no-cache",
        },
    )


async def _handle_healthz(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _handle_state_placeholder(_request: web.Request) -> web.Response:
    """Placeholder for the SSE channel. Slice-11 swaps this for a real
    stream; until then it answers with an empty object so the frontend
    can probe the route without 404-ing."""
    return web.json_response({})


def _make_api_state_handler(cfg: Any):
    async def _handler(request: web.Request) -> web.Response:
        if_none_match = request.headers.get("If-None-Match")
        try:
            return _state_response(cfg, if_none_match)
        except Exception:  # noqa: BLE001 — defence in depth
            log.exception("/api/state handler failed")
            return web.Response(status=500, text="internal error")
    return _handler


async def _handle_aggregates_get(request: web.Request) -> web.Response:
    """Return the current aggregator snapshot.

    Reads are O(buckets) — cheap. The snapshot is JSON-serialised inline so
    the same shape will be the SSE ``metric-tick`` payload in slice-11.
    """
    agg: Aggregator = request.app[APP_KEY_AGGREGATOR]
    try:
        return web.json_response(agg.snapshot())
    except Exception:  # noqa: BLE001
        log.exception("/aggregates GET failed")
        return web.Response(status=500, text="internal error")


async def _handle_aggregates_reset(request: web.Request) -> web.Response:
    """Drop in-memory aggregates and rebuild from disk.

    Used by Settings → Advanced → Reset Aggregates when a developer wants
    to repair a divergence between the displayed totals and what the JSONL
    files say. Operates only on RAM + disk reads — nothing destructive.
    """
    agg: Aggregator = request.app[APP_KEY_AGGREGATOR]
    sprints_root: Path = request.app[APP_KEY_SPRINTS_ROOT]
    try:
        agg.reset()
        count, _positions = replay_all(sprints_root, agg)
    except Exception:  # noqa: BLE001
        log.exception("/aggregates/reset failed")
        return web.Response(status=500, text="internal error")
    return web.json_response({"status": "ok", "events_replayed": count})


def _make_index_handler(dist_dir: Path):
    """Serve the built SPA's ``index.html`` at ``/``. If the bundle hasn't
    been built yet the route 404s with a hint so the dev knows to run
    ``pnpm --dir web build`` (or use the Vite dev server)."""
    async def _handler(_request: web.Request) -> web.Response:
        index = dist_dir / "index.html"
        if not index.is_file():
            return web.Response(
                status=404,
                text=(
                    "web/dist/index.html not found — run "
                    "`pnpm --dir web build` first, or use the Vite dev "
                    "server on :5173 during development."
                ),
            )
        return web.FileResponse(index)
    return _handler


def _resolve_sprints_root(cfg: Any) -> Path:
    """Best-effort: support both real ``NamlConfig`` (``sprints_path``) and
    the stub config used by tests (only ``repo_root``)."""
    sprints_path = getattr(cfg, "sprints_path", None)
    if sprints_path is not None:
        return Path(sprints_path)
    repo_root = Path(getattr(cfg, "repo_root", "."))
    return repo_root / ".naml" / "sprints"


def build_app(
    cfg: Any,
    *,
    web_dist: Path | None = None,
    aggregator: Aggregator | None = None,
    cold_start: bool = True,
) -> web.Application:
    """Construct the aiohttp ``Application`` without starting it.

    Exposed so tests can use ``aiohttp.test_utils`` to drive the app on a
    free port without standing up the full ``serve()`` lifecycle.

    Cold-start replay runs synchronously here — bounded by disk I/O on the
    handful-to-couple-hundred JSONL files a project accumulates. Pass
    ``cold_start=False`` in tests that don't want any disk replay.
    """
    app = web.Application()

    sprints_root = _resolve_sprints_root(cfg)
    agg = aggregator if aggregator is not None else Aggregator()
    if cold_start:
        try:
            import time
            t0 = time.perf_counter()
            count, _positions = replay_all(sprints_root, agg)
            elapsed = time.perf_counter() - t0
            log.info(
                "cold start replay: %d events in %dms",
                count,
                int(elapsed * 1000),
            )
        except Exception:  # noqa: BLE001
            log.exception("cold-start replay failed; aggregator left empty")

    app[APP_KEY_AGGREGATOR] = agg
    app[APP_KEY_SPRINTS_ROOT] = sprints_root

    app.router.add_get("/healthz", _handle_healthz)
    app.router.add_get("/api/state", _make_api_state_handler(cfg))
    app.router.add_get("/state", _handle_state_placeholder)
    app.router.add_get("/aggregates", _handle_aggregates_get)
    app.router.add_post("/aggregates/reset", _handle_aggregates_reset)

    if web_dist is None:
        repo_root = Path(getattr(cfg, "repo_root", ".")).resolve()
        web_dist = repo_root / "web" / "dist"

    app.router.add_get("/", _make_index_handler(web_dist))
    # Static assets emitted by Vite (hashed JS/CSS, etc.) live under
    # /assets/*. Only mount the static handler if the directory exists —
    # aiohttp refuses to add_static on a missing path.
    assets_dir = web_dist / "assets"
    if assets_dir.is_dir():
        app.router.add_static("/assets/", path=assets_dir, show_index=False)

    return app


def serve(
    cfg: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Block on the server. Ctrl-C exits cleanly."""
    app = build_app(cfg)
    log.info("naml cockpit listening on http://%s:%d", host, port)
    web.run_app(app, host=host, port=port, print=None)
