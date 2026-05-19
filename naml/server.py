"""Async HTTP server for the V2 cockpit.

Replaces the Phase-5 stopgap ``naml/web.py`` (stdlib ``http.server``). The
cockpit needs to hold long-lived SSE connections (slice-11), so the server
is built on aiohttp with a single asyncio event loop.

Routes:

- ``GET /healthz``         → ``{"status": "ok"}`` (liveness)
- ``GET /api/state``       → ``naml.project_state.build_hierarchy(cfg)`` with
                             weak ETag + ``If-None-Match`` 304 short-circuit.
- ``GET /state``           → ``{}`` placeholder kept for ``naml status``
                             callers; real cockpit telemetry rides on
                             ``/events``.
- ``GET /events``          → SSE stream (slice-11). Emits a ``snapshot``
                             event first, ``ping`` every 2s, and
                             ``state-update`` whenever a sprint/slice state
                             file changes. Honours ``Last-Event-ID``.
- ``GET /aggregates``      → ``Aggregator.snapshot()`` — token/cost rollups.
- ``POST /aggregates/reset`` → recompute aggregates from disk (Settings →
                             Advanced → Reset Aggregates).
- ``POST /intervene/{id}`` → drawer intervention endpoint (slice-7).
                             ``?action=`` is one of ``hold``, ``fail``,
                             ``skip``, ``open-terminal``. The first three
                             are recorded as intent and answered 200; the
                             real state transitions land in slice-14.
                             ``open-terminal`` shells out to
                             ``open -a Terminal`` (macOS) so the user can
                             ``claude --resume`` a paused slice.
- ``GET /``                → served from ``web/dist/`` when the bundle exists;
                             404 otherwise (Vite owns development).

The aggregator is owned by the ``Application`` instance — one per server.
Cold-start replay runs in :func:`build_app` so the snapshot is warm before
the first request lands.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from . import project_state as project_state_mod
from . import state as state_mod
from .aggregator import Aggregator
from .sse import Broadcaster, SSEEvent, encode_event
from .watcher import replay_all


log = logging.getLogger("naml.server")


# How often the state-file poller scans for mtime changes. 50ms keeps the
# end-to-end "file write -> SSE event" latency comfortably under the 200ms
# acceptance threshold while staying cheap on a single-developer laptop.
STATE_WATCH_INTERVAL_S = 0.05

# Heartbeat cadence. The browser side flips to SLOW after 5s without an
# event and to LOST after 15s — keep this well below 5s.
HEARTBEAT_INTERVAL_S = 2.0


# aiohttp app[key] entries — typed AppKey instances dodge aiohttp 3.10's
# NotAppKeyWarning and give callers proper static-type hints.
APP_KEY_AGGREGATOR: web.AppKey[Aggregator] = web.AppKey(
    "naml_aggregator", Aggregator
)
APP_KEY_SPRINTS_ROOT: web.AppKey[Path] = web.AppKey(
    "naml_sprints_root", Path
)
BROADCASTER_KEY: web.AppKey[Broadcaster] = web.AppKey("broadcaster", Broadcaster)
HEARTBEAT_INTERVAL_KEY: web.AppKey[float] = web.AppKey("heartbeat_interval", float)
STATE_WATCH_INTERVAL_KEY: web.AppKey[float] = web.AppKey("state_watch_interval", float)
HEARTBEAT_TASK_KEY: web.AppKey[Any] = web.AppKey("heartbeat_task", object)
WATCHER_TASK_KEY: web.AppKey[Any] = web.AppKey("watcher_task", object)


# Claude session-ids are UUID-shaped (32 hex chars + 4 dashes). We accept
# anything that fits the [hex|dash] alphabet up to a sensible cap so future
# session-id formats don't need a re-deploy, while still rejecting payloads
# that could contain shell or AppleScript metacharacters — the spawn path
# interpolates this value into a `claude --resume <id>` shell command run
# inside an AppleScript ``do script`` literal, where any quote or semicolon
# would let an attacker who can reach the local ``/intervene`` endpoint
# (e.g. a malicious page visited in the user's browser, since the server
# binds to 127.0.0.1 with no CSRF token) execute arbitrary commands.
_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F-]{1,128}$")


# --- /api/state (kept for backwards-compat with naml status) --------------




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
    """Legacy probe surface — returns ``{}``. Live telemetry rides on
    ``/events``; this route stays so existing ``naml status`` callers
    don't 404."""
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


# --- /events: SSE stream --------------------------------------------------

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _build_snapshot(cfg: Any, *, aggregator: Any | None = None) -> dict[str, Any]:
    """Compose the payload broadcast as the first SSE event on connect.

    Shape:

        {
            "ts": "<ISO 8601>",
            "sprints": { <sprint_id>: { sprint state... } },
            "slices":  { "<sprint_id>::<slice_id>": { slice state... } },
            "aggregates": <aggregator.snapshot() or empty fixtures>,
        }

    The aggregator is optional: slice-10 produces an in-memory aggregator
    that we read here when present. Slice-11 itself does not require it —
    cost timeline values show fixtures until slice-10 merges in.
    """
    sprints: dict[str, Any] = {}
    slices: dict[str, Any] = {}

    sprints_path = Path(getattr(cfg, "sprints_path", "."))
    if sprints_path.is_dir():
        for sprint_dir in sorted(sprints_path.iterdir()):
            if not sprint_dir.is_dir():
                continue
            sprint_state = state_mod.load_sprint_state(sprint_dir)
            if sprint_state is None:
                continue
            sprints[sprint_state.sprint_id] = sprint_state.to_dict()

            for slice_id in sprint_state.slices.keys():
                status = state_mod.load_slice_status(sprint_dir, slice_id)
                if status is None:
                    continue
                slices[f"{sprint_state.sprint_id}::{slice_id}"] = status.to_dict()

    if aggregator is not None and hasattr(aggregator, "snapshot"):
        try:
            aggregates = aggregator.snapshot()
        except Exception:  # noqa: BLE001 — never let aggregator faults break SSE
            log.exception("aggregator.snapshot() raised; emitting empty aggregates")
            aggregates = {}
    else:
        aggregates = {}

    return {
        "ts": _now_iso(),
        "sprints": sprints,
        "slices": slices,
        "aggregates": aggregates,
    }


def _parse_last_event_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _make_events_handler(cfg: Any):
    async def _handler(request: web.Request) -> web.StreamResponse:
        broadcaster = request.app[BROADCASTER_KEY]
        aggregator = request.app.get(APP_KEY_AGGREGATOR)
        last_id = _parse_last_event_id(request.headers.get("Last-Event-ID"))

        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                # Disable proxy/CDN buffering — irrelevant on 127.0.0.1 but
                # cheap insurance if the cockpit is ever fronted.
                "X-Accel-Buffering": "no",
            },
        )
        # SSE is a stream of small writes — chunked encoding lets each event
        # flush immediately. ``write_headers`` happens on ``prepare``.
        resp.enable_chunked_encoding()
        await resp.prepare(request)

        queue, replay = broadcaster.register(last_event_id=last_id)

        try:
            # Replay or snapshot, exclusive: if Last-Event-ID gave us a
            # buffered slice we send those (the client already has earlier
            # state). Otherwise the client is fresh and needs a snapshot.
            if last_id is not None and replay:
                for ev in replay:
                    await resp.write(encode_event(ev))
            else:
                snapshot_data = _build_snapshot(cfg, aggregator=aggregator)
                # The snapshot rides at the broadcaster's current id so the
                # client's Last-Event-ID on reconnect is well-defined. If
                # nothing has been published yet we just use 0.
                snap = SSEEvent(
                    id=broadcaster.current_id,
                    event="snapshot",
                    data=snapshot_data,
                )
                await resp.write(encode_event(snap))

            while True:
                ev = await queue.get()
                if ev is None:
                    # Broadcaster dropped us for being slow.
                    log.info("SSE client dropped (overflow)")
                    break
                await resp.write(encode_event(ev))
        except (asyncio.CancelledError, ConnectionResetError):
            # Client disconnected — normal path.
            raise
        finally:
            broadcaster.unregister(queue)

        return resp

    return _handler


# --- /intervene: drawer intervention endpoint (slice-7) ------------------

_VALID_ACTIONS = ("hold", "fail", "skip", "open-terminal")


def _slice_worktree(cfg: Any, slice_id: str) -> Path | None:
    """Best-effort lookup of the worktree path for a slice. Returns ``None``
    if the project state hierarchy doesn't know about the slice yet — the
    ``open-terminal`` handler then falls back to the repo root."""
    try:
        payload = project_state_mod.build_hierarchy(cfg)
    except Exception:  # noqa: BLE001 — telemetry-only path
        return None
    sprints = payload.get("sprints") if isinstance(payload, dict) else None
    if not isinstance(sprints, list):
        return None
    for sprint in sprints:
        for sl in sprint.get("slices", []) if isinstance(sprint, dict) else []:
            if not isinstance(sl, dict):
                continue
            if sl.get("id") == slice_id or sl.get("slice_id") == slice_id:
                wt = sl.get("worktree") or sl.get("worktree_path")
                if wt:
                    return Path(str(wt))
    return None


_OSASCRIPT_RUNNER = (
    'on run argv\n'
    '    set wt to item 1 of argv\n'
    '    set sid to item 2 of argv\n'
    '    set cmd to "cd " & quoted form of wt & " && claude --resume " & sid\n'
    '    tell application "Terminal" to do script cmd\n'
    'end run'
)


def _spawn_terminal(worktree: Path, session_id: str | None) -> bool:
    """Open a new Terminal window at ``worktree`` running ``claude --resume``.
    Returns ``True`` if the shell-out launched, ``False`` otherwise. macOS
    only — on Linux/Windows the cockpit currently can't honour the action.

    ``session_id`` must already be validated against ``_SESSION_ID_RE`` by
    the caller; we re-validate as defence-in-depth so this function is safe
    to call from any future code path. The worktree path is passed through
    AppleScript's ``quoted form of`` rather than f-stringed into the shell
    literal, so it can't break out of the inner string either.
    """
    if sys.platform != "darwin":
        return False
    if not shutil.which("open"):
        return False
    if session_id:
        if not _SESSION_ID_RE.fullmatch(session_id):
            log.warning(
                "rejecting open-terminal: session_id failed validation",
            )
            return False
        try:
            subprocess.Popen(  # noqa: S603 — argv list, no shell=True
                [
                    "osascript",
                    "-e",
                    _OSASCRIPT_RUNNER,
                    str(worktree),
                    session_id,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            return False
    # No session yet — just open a Terminal at the worktree.
    try:
        subprocess.Popen(  # noqa: S603 — argv list, no shell=True
            ["open", "-a", "Terminal", "-n", str(worktree)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def _make_intervene_handler(cfg: Any):
    """Drawer intervention endpoint. The real state-machine wiring for
    ``hold`` lands in slice-14; until then we 200 so the UI can show its
    flash. ``open-terminal`` does shell out today."""

    async def _handler(request: web.Request) -> web.Response:
        slice_id = request.match_info.get("slice_id", "").strip()
        action = (request.rel_url.query.get("action") or "").strip()

        if not slice_id:
            return web.json_response({"error": "missing slice_id"}, status=400)
        if action not in _VALID_ACTIONS:
            return web.json_response(
                {
                    "error": f"unknown action {action!r}",
                    "valid": list(_VALID_ACTIONS),
                },
                status=400,
            )

        log.info("intervene: slice=%s action=%s", slice_id, action)

        if action == "open-terminal":
            session_id = request.rel_url.query.get("session_id")
            # Reject malformed session-ids at the boundary so the spawn
            # path can never see anything that would let an attacker
            # break out of the AppleScript / shell quoting layers.
            if session_id is not None and not _SESSION_ID_RE.fullmatch(session_id):
                return web.json_response(
                    {
                        "error": "invalid session_id",
                        "expected": "^[0-9a-fA-F-]{1,128}$",
                    },
                    status=400,
                )
            worktree = _slice_worktree(cfg, slice_id) or Path(
                getattr(cfg, "repo_root", ".")
            )
            launched = _spawn_terminal(worktree, session_id)
            return web.json_response(
                {
                    "status": "ok" if launched else "noop",
                    "action": action,
                    "slice_id": slice_id,
                    "launched": launched,
                    "worktree": str(worktree),
                },
                status=200 if launched else 202,
            )

        # hold / fail / skip — stubbed until slice-14 wires real semantics.
        return web.json_response(
            {
                "status": "accepted",
                "action": action,
                "slice_id": slice_id,
                "note": "stub handler — real state transition lands in slice-14",
            },
            status=200,
        )

    return _handler


# --- background tasks: heartbeat + state-file watcher ---------------------

async def _heartbeat_loop(app: web.Application) -> None:
    """Publish a ``ping`` event every :data:`HEARTBEAT_INTERVAL_S` seconds.

    The browser uses this to derive sync status: 5s without any event flips
    to SLOW, 15s flips to LOST. Pinging is cheap, so this never sleeps with
    catch-up logic — it just fires at the cadence.
    """
    broadcaster = app[BROADCASTER_KEY]
    interval = app.get(HEARTBEAT_INTERVAL_KEY, HEARTBEAT_INTERVAL_S)
    try:
        while True:
            await asyncio.sleep(interval)
            broadcaster.publish("ping", {"t": _now_iso()})
    except asyncio.CancelledError:
        return


def _read_status_payload(path: Path) -> dict[str, Any] | None:
    """Parse a state JSON file. Returns ``None`` on any I/O or JSON error.

    The watcher hot-loop calls this on every mtime bump — we never want a
    half-written file to crash the loop. ``state.save_*`` writes atomically
    via tmp-rename so the race window is tiny, but readers must still cope.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


async def _state_watcher_loop(app: web.Application) -> None:
    """Poll ``sprints/*/state/*.json`` for mtime changes; publish updates.

    First-pass populates the mtime map silently so reconnecting clients
    don't immediately get a flood of state-updates for every file already
    captured in their snapshot. Subsequent iterations emit a
    ``state-update`` per changed file.
    """
    broadcaster = app[BROADCASTER_KEY]
    sprints_path = app[APP_KEY_SPRINTS_ROOT]
    interval = app.get(STATE_WATCH_INTERVAL_KEY, STATE_WATCH_INTERVAL_S)

    mtimes: dict[Path, float] = {}
    first_pass = True

    try:
        while True:
            # Sort for deterministic ordering — helps tests stay stable.
            try:
                sprint_dirs = sorted(p for p in sprints_path.iterdir() if p.is_dir())
            except (OSError, FileNotFoundError):
                sprint_dirs = []

            for sprint_dir in sprint_dirs:
                state_dir = sprint_dir / state_mod.STATE_DIRNAME
                if not state_dir.is_dir():
                    continue

                try:
                    files = sorted(state_dir.iterdir())
                except OSError:
                    continue

                for path in files:
                    if not path.is_file():
                        continue
                    name = path.name
                    if name == state_mod.SPRINT_STATE_FILENAME:
                        kind = "sprint"
                        event_id = sprint_dir.name
                    elif name.endswith(".status.json"):
                        kind = "slice"
                        slice_id = name[: -len(".status.json")]
                        event_id = f"{sprint_dir.name}::{slice_id}"
                    else:
                        continue

                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    prev = mtimes.get(path)
                    if prev == mtime:
                        continue
                    mtimes[path] = mtime
                    if first_pass:
                        continue

                    delta = _read_status_payload(path)
                    if delta is None:
                        continue
                    broadcaster.publish(
                        "state-update",
                        {"kind": kind, "id": event_id, "delta": delta},
                    )

            first_pass = False
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
    except asyncio.CancelledError:
        return


# --- app factory ----------------------------------------------------------

def build_app(
    cfg: Any,
    *,
    web_dist: Path | None = None,
    aggregator: Aggregator | None = None,
    broadcaster: Broadcaster | None = None,
    heartbeat_interval: float | None = None,
    state_watch_interval: float | None = None,
    cold_start: bool = True,
) -> web.Application:
    """Construct the aiohttp ``Application`` without starting it.

    Exposed so tests can use ``aiohttp.test_utils`` to drive the app on a
    free port without standing up the full ``serve()`` lifecycle.

    Cold-start replay (slice-10) runs synchronously — bounded by disk I/O on
    the handful-to-couple-hundred JSONL files a project accumulates. Pass
    ``cold_start=False`` in tests that don't want disk replay. The SSE
    broadcaster and background tasks (slice-11) initialize lazily on
    ``on_startup`` so tests using ``aiohttp.test_utils`` get a real loop.

    Test-only kwargs:

    - ``broadcaster``: lets tests inject a pre-built broadcaster with a
      shorter ring buffer / queue limit.
    - ``heartbeat_interval`` / ``state_watch_interval``: speed up tests.
    """
    app = web.Application()

    # Slice-10 — aggregator + cold-start replay.
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

    # Slice-11 — SSE broadcaster + intervals.
    app[BROADCASTER_KEY] = broadcaster or Broadcaster()
    if heartbeat_interval is not None:
        app[HEARTBEAT_INTERVAL_KEY] = heartbeat_interval
    if state_watch_interval is not None:
        app[STATE_WATCH_INTERVAL_KEY] = state_watch_interval

    app.router.add_get("/healthz", _handle_healthz)
    app.router.add_get("/api/state", _make_api_state_handler(cfg))
    app.router.add_get("/state", _handle_state_placeholder)
    app.router.add_get("/events", _make_events_handler(cfg))
    app.router.add_get("/aggregates", _handle_aggregates_get)
    app.router.add_post("/aggregates/reset", _handle_aggregates_reset)
    app.router.add_post("/intervene/{slice_id}", _make_intervene_handler(cfg))

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

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    return app


async def _on_startup(app: web.Application) -> None:
    """Spin up the background heartbeat and state-watcher tasks."""
    app[HEARTBEAT_TASK_KEY] = asyncio.create_task(
        _heartbeat_loop(app), name="naml-heartbeat",
    )
    app[WATCHER_TASK_KEY] = asyncio.create_task(
        _state_watcher_loop(app), name="naml-state-watcher",
    )


async def _on_cleanup(app: web.Application) -> None:
    """Cancel background tasks and wait for them to unwind."""
    for key in (HEARTBEAT_TASK_KEY, WATCHER_TASK_KEY):
        task = app.get(key)
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def serve(
    cfg: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    aggregator: Any | None = None,
) -> None:
    """Block on the server. Ctrl-C exits cleanly."""
    app = build_app(cfg, aggregator=aggregator)
    log.info("naml cockpit listening on http://%s:%d", host, port)
    web.run_app(app, host=host, port=port, print=None)
