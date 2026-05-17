"""Local web cockpit for agents-orchestrator.

Stdlib-only HTTP server (``http.server.ThreadingHTTPServer``). Serves a single
HTML page and a tiny JSON API the page polls every few seconds.

Routes:
    GET  /                  the cockpit page
    GET  /api/state         JSON: current run, recent runs, queue, Pro window
    POST /api/open          {"session_id": "..."} → open Terminal with claude --resume

Designed to be reachable only from localhost — there is no auth, on purpose.
Bind defaults to 127.0.0.1.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import claude_session, config, github_ops


# --- state assembly -------------------------------------------------------

def _parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _read_runs(limit: int = 20) -> list[dict]:
    cfg = config.load()
    if not cfg.runs_jsonl.exists():
        return []
    runs: list[dict] = []
    for line in cfg.runs_jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs[-limit:][::-1]


def _read_current() -> dict | None:
    """Legacy single-lane fallback — read current.json if no lane files exist."""
    cfg = config.load()
    if not cfg.current_json.exists():
        return None
    try:
        return json.loads(cfg.current_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_currents() -> list[dict]:
    """Read every lane state file. One entry per active lane.

    Falls back to the legacy ``current.json`` when no lane files exist
    (e.g., for an older orchestrator process still running mid-deploy).
    """
    cfg = config.load()
    if not cfg.log_dir.exists():
        return []
    lanes: list[dict] = []
    for path in sorted(cfg.log_dir.glob("current.lane*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        lanes.append(data)
    if not lanes:
        legacy = _read_current()
        if legacy:
            legacy.setdefault("lane", 1)
            lanes.append(legacy)
    lanes.sort(key=lambda d: int(d.get("lane", 1)))
    return lanes


# Cache the queue read for ~5s so multi-tab polling doesn't hammer `gh`.
_QUEUE_CACHE: dict[str, Any] = {"at": 0.0, "issues": [], "error": None}
_QUEUE_LOCK = threading.Lock()
_QUEUE_TTL = 5.0


def _read_queue_cached() -> tuple[list[dict], str | None]:
    now = time.time()
    with _QUEUE_LOCK:
        if now - _QUEUE_CACHE["at"] < _QUEUE_TTL:
            return _QUEUE_CACHE["issues"], _QUEUE_CACHE["error"]
        try:
            issues = github_ops.list_ready_issues()
            _QUEUE_CACHE.update(at=now, issues=issues, error=None)
            return issues, None
        except github_ops.GhError as exc:
            err = str(exc)
            _QUEUE_CACHE.update(at=now, issues=[], error=err)
            return [], err


def _aggregate_run_stats(runs: list[dict]) -> dict:
    """Aggregate token/cost stats across orchestrator runs, plus a
    chart-ready time series (one point per run).

    The chart bars are reversed to oldest→newest so the chart reads
    left-to-right in chronological order.
    """
    if not runs:
        return {
            "count": 0,
            "tokens_total": 0, "tokens_avg": 0,
            "cost_total": 0.0, "cost_avg": 0.0,
            "chart": [],
        }
    total_tokens = 0
    total_cost = 0.0
    chart: list[dict] = []
    for r in reversed(runs):  # oldest first for the chart
        u = r.get("usage") or {}
        # The runner writes input_tokens / output_tokens / cache_read_input_tokens
        # / cache_creation_input_tokens. claude_session writes input_tokens /
        # output_tokens / cache_read_tokens / cache_write_5m_tokens /
        # cache_write_1h_tokens. Sum BOTH key namings — they don't collide
        # (a record uses one or the other, never both).
        tokens = (
            int(u.get("input_tokens", 0) or 0)
            + int(u.get("output_tokens", 0) or 0)
            + int(u.get("cache_read_input_tokens", 0) or 0)
            + int(u.get("cache_creation_input_tokens", 0) or 0)
            + int(u.get("cache_read_tokens", 0) or 0)
            + int(u.get("cache_write_5m_tokens", 0) or 0)
            + int(u.get("cache_write_1h_tokens", 0) or 0)
        )
        cost = float(u.get("total_cost_usd", 0.0) or 0.0)
        total_tokens += tokens
        total_cost += cost
        chart.append({
            "run_id": r.get("run_id"),
            "issue_number": r.get("issue_number"),
            "issue_title": r.get("issue_title"),
            "batch_id": r.get("batch_id"),
            "session_id": r.get("session_id"),
            "outcome": r.get("outcome"),
            "started_at": r.get("started_at"),
            "duration_sec": r.get("duration_sec", 0),
            "tokens": tokens,
            "cost_usd": round(cost, 4),
        })
    n = len(runs)
    return {
        "count": n,
        "tokens_total": total_tokens,
        "tokens_avg": total_tokens // n if n else 0,
        "cost_total": round(total_cost, 4),
        "cost_avg": round(total_cost / n, 4) if n else 0,
        "chart": chart,
    }


def _usage_to_dict(u: claude_session.Usage, *, limit: int | None = None) -> dict:
    return {
        "ok": u.ok,
        "window_seconds": u.window_seconds,
        "total_tokens": u.total_tokens,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_tokens": u.cache_read_tokens,
        "cache_write_5m_tokens": u.cache_write_5m_tokens,
        "cache_write_1h_tokens": u.cache_write_1h_tokens,
        "cost_usd": u.cost_usd,
        "reset_in_seconds": u.reset_in_seconds,
        "by_model": u.by_model,
        "limit": limit,
        "headroom": (max(0, limit - u.total_tokens) if limit else None),
        "pct_used": (round(100 * u.total_tokens / limit, 1)
                     if (limit and limit > 0) else None),
    }


def _build_state() -> dict:
    cfg = config.load()
    currents = _read_currents()
    # Legacy single-lane field. The UI prefers `currents` when present.
    current = currents[0] if currents else None
    runs = _read_runs(limit=20)
    queue, queue_err = _read_queue_cached()

    today = claude_session.today_usage()
    last30 = claude_session.thirty_day_usage()

    # Aggregate stats across all orchestrator runs in the ledger.
    run_stats = _aggregate_run_stats(runs)

    # Group queue by batch for the UI.
    queue_by_batch: dict[str | None, list[dict]] = {}
    for issue in queue:
        bid = github_ops.batch_id_of(issue)
        queue_by_batch.setdefault(bid, []).append(issue)
    queue_groups = [
        {"batch_id": bid, "issues": items}
        for bid, items in queue_by_batch.items()
    ]

    # Pull a short, human-recognizable label for the Claude account whose
    # transcripts we're summing — last path segment of the config dir.
    claude_account = cfg.claude_config_dir.name or str(cfg.claude_config_dir)

    # Same default-detect as runner._claude_env: if the toml's config_dir
    # is the default ~/.claude, OMIT the explicit env var from any
    # paste-able command. Setting it explicitly would surface "Not logged
    # in" because the keychain entry is keyed against the unset state.
    default_dir = Path.home() / ".claude"
    is_default_account = cfg.claude_config_dir.resolve() == default_dir.resolve()
    resume_env_prefix = (
        "" if is_default_account
        else f"CLAUDE_CONFIG_DIR={cfg.claude_config_dir} "
    )

    return {
        "repo": cfg.repo,
        "base_branch": cfg.base_branch,
        "stop_after": cfg.stop_after,
        "auto_review": cfg.auto_review,
        "dry_run": cfg.dry_run,
        "claude_account": claude_account,
        "claude_config_dir": str(cfg.claude_config_dir),
        "resume_env_prefix": resume_env_prefix,
        "repo_root": str(cfg.repo_root),
        "settings": {
            "pipeline.stop_after": cfg.stop_after,
            "pipeline.auto_review": cfg.auto_review,
            "claude.config_dir": str(cfg.claude_config_dir),
            "claude.session_token_limit": cfg.session_token_limit,
            "claude.weekly_token_limit": cfg.weekly_token_limit,
            "claude.session_reset_at": cfg.session_reset_at,
            "claude.weekly_reset_at": cfg.weekly_reset_at,
            "claude.cost_calibration": cfg.cost_calibration,
            "run.cap_minutes": cfg.run_cap_minutes,
            "run.max_retries": cfg.max_retries,
            "burst.parallel_lanes": cfg.parallel_lanes,
        },
        "now": datetime.now(timezone.utc).isoformat(),
        "current": current,
        "currents": currents,
        "parallel_lanes": cfg.parallel_lanes,
        "current_cap_minutes": cfg.run_cap_minutes,
        "runs": runs,
        "queue": queue,
        "queue_groups": queue_groups,
        "queue_error": queue_err,
        "usage": {
            "today": _usage_to_dict(today),
            "last_30_days": _usage_to_dict(last30),
        },
        "run_stats": run_stats,
    }


# --- ETag ----------------------------------------------------------------

# Fields that drift every poll without anything actually changing. Strip
# before hashing so a quiet system returns 304 forever (until real state moves).
_ETAG_DRIFT_KEYS = ("now",)
_ETAG_DRIFT_USAGE_KEYS = ("reset_in_seconds",)


def _payload_etag(payload: dict) -> str:
    """Hash a stable projection of the state — excludes wall-clock drift."""
    stripped = {k: v for k, v in payload.items() if k not in _ETAG_DRIFT_KEYS}
    usage = stripped.get("usage") or {}
    if usage:
        usage_clean: dict[str, Any] = {}
        for window, block in usage.items():
            if isinstance(block, dict):
                usage_clean[window] = {k: v for k, v in block.items()
                                       if k not in _ETAG_DRIFT_USAGE_KEYS}
            else:
                usage_clean[window] = block
        stripped["usage"] = usage_clean
    blob = json.dumps(stripped, sort_keys=True, default=str).encode("utf-8")
    return '"' + hashlib.sha1(blob).hexdigest() + '"'


# --- HTTP handler ---------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Browser closed the connection mid-response (refresh, tab close,
            # polling overlap). Normal; not worth a stack trace.
            pass

    def _send_json(self, payload: Any, status: int = 200) -> None:
        self._send_bytes(
            json.dumps(payload).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _send_304(self, etag: str) -> None:
        try:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json_with_etag(self, payload: Any, etag: str) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_html(self, body: str, status: int = 200) -> None:
        self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8", status)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — stdlib signature
        # Quieter logs — only show errors.
        if args and isinstance(args[0], str) and args[0].startswith(("4", "5")):
            sys.stderr.write("[ao-web] " + (format % args) + "\n")

    def handle_one_request(self) -> None:
        # Suppress the noisy traceback when a client hangs up mid-request.
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802 — stdlib signature
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(_INDEX_HTML)
            return
        if self.path == "/api/state":
            try:
                payload = _build_state()
            except Exception as exc:  # last-ditch — surface to UI
                self._send_json({"error": str(exc)}, status=500)
                return
            etag = _payload_etag(payload)
            client_etag = self.headers.get("If-None-Match", "").strip()
            if client_etag and client_etag == etag:
                self._send_304(etag)
                return
            self._send_json_with_etag(payload, etag)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/config":
            self._handle_config_post()
            return
        if self.path == "/api/open_log":
            self._handle_open_log()
            return
        self._send_json({"error": "not found"}, status=404)

    def _handle_open_log(self) -> None:
        """Open a run log file via `open` (macOS default app)."""
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, status=400)
            return
        path = str(payload.get("path", "") or "")
        if not path:
            self._send_json({"error": "path required"}, status=400)
            return
        # Constrain to the log dir to prevent open-anything abuse.
        cfg = config.load()
        target = Path(path).expanduser().resolve()
        log_root = cfg.log_dir.resolve()
        try:
            target.relative_to(log_root)
        except ValueError:
            self._send_json({"error": "path outside log dir"}, status=400)
            return
        if not target.exists():
            self._send_json({"error": "log file not found"}, status=404)
            return
        try:
            subprocess.run(["open", str(target)], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            self._send_json({"error": f"open failed: {exc.stderr.strip()}"}, status=500)
            return
        self._send_json({"ok": True})

    def _handle_config_post(self) -> None:
        """Persist UI-editable settings back into .agents-orchestrator.toml.

        Only a whitelist of safe scalar fields is editable through the UI.
        Anything else has to be hand-edited in the file.
        """
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, status=400)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "payload must be a JSON object"}, status=400)
            return

        try:
            edits = _validate_config_edits(payload)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        if not edits:
            self._send_json({"ok": True, "changed": 0})
            return

        try:
            path = config.config_path()
            config.edit_toml(path, edits)
            config.reset_cache()
        except Exception as exc:
            self._send_json({"error": f"could not write TOML: {exc}"}, status=500)
            return
        self._send_json({"ok": True, "changed": len(edits), "path": str(path)})


# Editable-field whitelist with type validation. Keys here are
# "<section>.<key>" as the form sends them; values are converted to the
# correct Python type before being passed to edit_toml.
def _validate_config_edits(payload: dict) -> dict[tuple[str | None, str], Any]:
    edits: dict[tuple[str | None, str], Any] = {}
    for key, raw in payload.items():
        if not isinstance(key, str) or "." not in key:
            raise ValueError(f"unknown setting: {key!r}")
        section, name = key.split(".", 1)
        spec = _EDITABLE.get((section, name))
        if not spec:
            raise ValueError(f"setting {key!r} is not UI-editable")
        # Empty string / None means: clear the override (re-comment the line).
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            edits[(section, name)] = None
            continue
        try:
            v = spec(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{key}: {exc}") from None
        edits[(section, name)] = v
    return edits


def _enum(*choices: str):
    def _check(v):
        if v not in choices:
            raise ValueError(f"must be one of {choices}")
        return v
    return _check


def _pos_int(v):
    n = int(v)
    if n <= 0:
        raise ValueError("must be > 0")
    return n


def _nonneg_int(v):
    n = int(v)
    if n < 0:
        raise ValueError("must be >= 0")
    return n


def _pos_float(v):
    f = float(v)
    if f <= 0:
        raise ValueError("must be > 0")
    return f


def _bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in {"1", "true", "yes", "on"}
    return bool(v)


def _path_str(v):
    s = str(v).strip()
    if not s:
        raise ValueError("path required")
    return s


# (section, key) → coercer
def _iso_dt(v):
    """Accept a datetime-local string (`YYYY-MM-DDTHH:MM`) OR a tz-aware ISO
    string. Store it as a NAIVE local-time ISO string (no offset). The form
    sends local time, the reader (`_parse_reset_iso`) interprets naive
    values as local — this avoids any TZ drift between client and server.
    """
    s = str(v).strip()
    if not s:
        raise ValueError("empty datetime")
    try:
        from datetime import datetime as _dt
        s2 = s.replace("Z", "+00:00")
        dt = _dt.fromisoformat(s2)
        # If tz-aware: convert into the server's local TZ, then strip the
        # offset for clean naive storage. If already naive: store as-is.
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.isoformat(timespec="seconds")
    except ValueError:
        raise ValueError("must be ISO 8601 (e.g. 2026-05-17T09:50:00)")


def _lanes_int(v):
    n = int(v)
    if n < 1 or n > 4:
        raise ValueError("parallel_lanes must be between 1 and 4")
    return n


_EDITABLE: dict[tuple[str, str], Any] = {
    ("pipeline", "stop_after"): _enum("pr", "review", "merge"),
    ("pipeline", "auto_review"): _bool,
    ("claude", "config_dir"): _path_str,
    ("claude", "session_token_limit"): _pos_int,
    ("claude", "weekly_token_limit"): _pos_int,
    ("claude", "session_reset_at"): _iso_dt,
    ("claude", "weekly_reset_at"): _iso_dt,
    ("claude", "cost_calibration"): _pos_float,
    ("run", "cap_minutes"): _pos_int,
    ("run", "max_retries"): _nonneg_int,
    ("burst", "parallel_lanes"): _lanes_int,
}


# --- index.html (embedded) ------------------------------------------------

_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>agents-orchestrator</title>
  <style>
    /* === Theme tokens === */
    :root {
      /* Dark is the default. Light flips via [data-theme="light"] or
         prefers-color-scheme below. */
      --bg:          #0e0e10;
      --panel:       #18181b;
      --panel-2:     #1f1f23;
      --panel-hi:    rgba(255,255,255,0.04);
      --border:      #2a2a2f;
      --border-2:    #3a3a40;
      --text:        #e6e6ea;
      --muted:       #8a8a92;
      --muted-2:     #5b5b62;
      --accent:      #58a6ff;
      --accent-fg:   #0d1117;
      --green:       #3fb950;
      --yellow:      #d29922;
      --red:         #f85149;
      --purple:      #bc8cff;
      --bar-bg:      #2a2a2f;
      --bar-fill:    #ec8a5d;   /* coral — matches CodexBar */
      --bar-failed:  #f85149;
      --bar-needs:   #d29922;
      --bar-good:    #ec8a5d;
      --shadow:      0 12px 40px rgba(0,0,0,0.5);
      --backdrop:    rgba(0,0,0,0.55);
    }
    :root[data-theme="light"], :root[data-theme="light"] body {
      --bg:          #ffffff;
      --panel:       #f7f7f9;
      --panel-2:     #efeff2;
      --panel-hi:    rgba(0,0,0,0.025);
      --border:      #e3e3e7;
      --border-2:    #d2d2d7;
      --text:        #1d1d1f;
      --muted:       #6e6e73;
      --muted-2:     #a1a1a6;
      --accent:      #0071e3;
      --accent-fg:   #ffffff;
      --green:       #1f8a3a;
      --yellow:      #9a6b00;
      --red:         #c0392b;
      --purple:      #7e3ff2;
      --bar-bg:      #e3e3e7;
      --shadow:      0 8px 28px rgba(0,0,0,0.10);
      --backdrop:    rgba(0,0,0,0.30);
    }
    @media (prefers-color-scheme: light) {
      :root:not([data-theme="dark"]):not([data-theme="light"]) {
        --bg:          #ffffff;
        --panel:       #f7f7f9;
        --panel-2:     #efeff2;
        --panel-hi:    rgba(0,0,0,0.025);
        --border:      #e3e3e7;
        --border-2:    #d2d2d7;
        --text:        #1d1d1f;
        --muted:       #6e6e73;
        --muted-2:     #a1a1a6;
        --accent:      #0071e3;
        --accent-fg:   #ffffff;
        --green:       #1f8a3a;
        --yellow:      #9a6b00;
        --red:         #c0392b;
        --purple:      #7e3ff2;
        --bar-bg:      #e3e3e7;
        --shadow:      0 8px 28px rgba(0,0,0,0.10);
        --backdrop:    rgba(0,0,0,0.30);
      }
    }

    /* === Reset / base === */
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 13px/1.55 -apple-system, BlinkMacSystemFont, "SF Pro Text",
            "Segoe UI", system-ui, sans-serif;
      -webkit-font-smoothing: antialiased;
      transition: background 0.15s ease, color 0.15s ease;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    button {
      background: var(--panel-2);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 5px 11px;
      font: 12px/1.4 inherit;
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s, transform 0.05s;
    }
    button:hover { background: var(--panel-hi); border-color: var(--border-2); }
    button:active { transform: translateY(1px); }
    button:disabled { opacity: 0.6; cursor: default; }
    button.primary {
      background: var(--accent); color: var(--accent-fg);
      border-color: var(--accent);
    }
    button.primary:hover { filter: brightness(1.05); }
    button.link {
      background: transparent; border: none; padding: 0;
      color: var(--accent); font-size: inherit;
    }
    button.link:hover { text-decoration: underline; }
    input, select, textarea {
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 5px 8px;
      font: 12px/1.4 inherit;
      font-variant-numeric: tabular-nums;
    }
    input:focus, select:focus, textarea:focus {
      outline: none; border-color: var(--accent);
    }

    /* === Layout === */
    .nav {
      position: sticky; top: 0; z-index: 10;
      background: color-mix(in srgb, var(--bg) 92%, transparent);
      backdrop-filter: saturate(180%) blur(10px);
      -webkit-backdrop-filter: saturate(180%) blur(10px);
      border-bottom: 1px solid var(--border);
    }
    .nav-inner {
      max-width: 980px; margin: 0 auto;
      display: flex; align-items: center; gap: 12px;
      padding: 12px 20px;
    }
    .nav h1 {
      margin: 0; font-size: 13px; font-weight: 600;
      letter-spacing: 0.4px; text-transform: uppercase;
      color: var(--accent);
    }
    .nav .repo { color: var(--muted); font-size: 12px; font-family: ui-monospace, "SF Mono", Menlo, monospace; }
    .nav .pills { display: flex; gap: 6px; align-items: center; margin-left: auto; }
    .nav .icon-btn {
      width: 30px; height: 30px;
      display: inline-flex; align-items: center; justify-content: center;
      padding: 0; border-radius: 8px;
      font-size: 14px;
    }
    .pill {
      display: inline-flex; align-items: center; gap: 4px;
      padding: 2px 9px; border-radius: 12px;
      background: var(--panel-2); border: 1px solid var(--border);
      color: var(--muted); font-size: 11px;
      font-family: ui-monospace, "SF Mono", Menlo, monospace;
    }
    .pill.accent { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 30%, var(--border)); }
    .pill.green  { color: var(--green);  border-color: color-mix(in srgb, var(--green) 30%, var(--border)); }
    .pill.yellow { color: var(--yellow); border-color: color-mix(in srgb, var(--yellow) 30%, var(--border)); }
    .pill.red    { color: var(--red);    border-color: color-mix(in srgb, var(--red) 30%, var(--border)); }
    .pill.purple { color: var(--purple); border-color: color-mix(in srgb, var(--purple) 30%, var(--border)); }

    .wrap { max-width: 980px; margin: 0 auto; padding: 22px 20px 64px; }

    .section {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
      margin-bottom: 14px;
    }
    .section h2 {
      font-size: 10px; font-weight: 700; letter-spacing: 0.6px;
      text-transform: uppercase; color: var(--muted);
      margin: 0 0 10px 0;
    }

    /* === Stat tiles === */
    .stat-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .stat {
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
    }
    .stat .label {
      font-size: 10px; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.5px;
      color: var(--muted);
    }
    .stat .value {
      font-size: 18px; font-weight: 600;
      color: var(--text);
      margin-top: 2px;
      font-variant-numeric: tabular-nums;
    }
    .stat .sub {
      font-size: 11px; color: var(--muted-2);
      margin-top: 2px;
      font-variant-numeric: tabular-nums;
    }

    /* === Bar chart === */
    .chart-filters {
      display: flex; flex-wrap: wrap; gap: 8px 16px;
      padding: 8px 0 0; margin-top: 8px;
      font-size: 11px;
      border-top: 1px solid var(--border);
    }
    .chart-filters .filter-group {
      display: flex; align-items: center; gap: 4px;
    }
    .chart-filters .filter-label {
      color: var(--muted-2);
      margin-right: 2px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-size: 10px;
    }
    .chart-filters .chip {
      padding: 3px 9px;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--muted);
      border-radius: 999px;
      cursor: pointer;
      font-size: 11px;
      transition: background 0.12s, color 0.12s, border-color 0.12s;
    }
    .chart-filters .chip:hover {
      color: var(--text);
      border-color: var(--accent);
    }
    .chart-filters .chip.active {
      background: var(--accent);
      color: var(--bg);
      border-color: var(--accent);
      font-weight: 600;
    }

    .chart {
      display: flex; align-items: stretch;  /* stretch so .bar-col fills height */
      gap: 3px;
      height: 100px;
      padding: 8px 0 0;
      margin-top: 8px;
    }
    .chart .bar-col {
      flex: 1;
      min-width: 5px;
      max-width: 22px;
      height: 100%;          /* explicit height so .bar-fill % heights resolve */
      display: flex; flex-direction: column;
      justify-content: flex-end;
      cursor: pointer;
      position: relative;
    }
    .chart .bar-fill {
      width: 100%;
      background: var(--bar-good);
      border-radius: 2px 2px 0 0;
      transition: opacity 0.12s, transform 0.12s;
    }
    .chart .bar-col:hover .bar-fill {
      opacity: 0.8;
      transform: translateY(-1px);
    }
    .chart .bar-fill.outcome-failed     { background: var(--bar-failed); }
    .chart .bar-fill.outcome-needs-info { background: var(--bar-needs); }
    .chart .bar-fill.outcome-stopped-at-pr,
    .chart .bar-fill.outcome-stopped-at-review,
    .chart .bar-fill.outcome-dry-run    { background: var(--yellow); }
    .chart .bar-fill.outcome-done       { background: var(--green); }
    .chart .bar-label {
      position: absolute;
      bottom: -16px;
      left: 50%;
      transform: translateX(-50%);
      font-size: 9px;
      color: var(--muted-2);
      font-family: ui-monospace, "SF Mono", Menlo, monospace;
      white-space: nowrap;
      pointer-events: none;
    }
    .chart-meta {
      display: flex; justify-content: space-between;
      font-size: 11px; color: var(--muted-2);
      padding: 22px 0 0;  /* room for the per-bar issue labels */
    }
    .chart-empty { color: var(--muted-2); padding: 20px 0; text-align: center; font-size: 12px; }

    /* === Run rows === */
    .row { display: flex; align-items: center; gap: 10px; padding: 6px 0; }
    .row + .row { border-top: 1px solid var(--border); }
    .grid3 { display: grid; grid-template-columns: auto 1fr auto; gap: 8px 12px; align-items: center; }
    .truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .small { font-size: 11px; }
    .dim   { color: var(--muted-2); }
    .mono  { font-family: ui-monospace, "SF Mono", Menlo, monospace; }
    .session-row {
      display: flex; align-items: center; gap: 8px;
      margin-top: 6px; padding-top: 6px;
      border-top: 1px dashed var(--border);
    }
    .session-row .mono { flex: 1; min-width: 0; color: var(--muted); font-size: 11px; }
    .session-row button { padding: 3px 9px; font-size: 11px; }

    /* Compact icon buttons used in session rows + run lists */
    .icon-btn {
      display: inline-flex; align-items: center; justify-content: center;
      width: 26px; height: 26px;
      padding: 0; border-radius: 6px;
      background: var(--bar-bg);
      border: 1px solid var(--border);
      color: var(--muted);
      cursor: pointer;
      transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease, transform 0.08s ease;
    }
    .icon-btn:hover {
      background: var(--panel-hover, var(--bar-bg));
      color: var(--text);
      border-color: var(--accent);
    }
    .icon-btn:active { transform: scale(0.94); }
    .icon-btn.copied {
      color: var(--green); border-color: var(--green);
    }
    .icon-btn svg { width: 14px; height: 14px; display: block; }
    .outcome { font-weight: 600; }
    .outcome.done             { color: var(--green); }
    .outcome.failed           { color: var(--red); }
    .outcome.stopped-at-pr,
    .outcome.stopped-at-review { color: var(--yellow); }
    .outcome.needs-info       { color: var(--accent); }
    .outcome.dry-run          { color: var(--yellow); }
    .empty { color: var(--muted-2); font-style: italic; padding: 6px 0; }

    /* Batch group card */
    .batch-list {
      margin: 8px 0 4px;
      padding: 8px 10px 4px;
      background: var(--panel-hi);
      border-left: 2px solid var(--purple);
      border-radius: 4px;
    }
    .batch-item + .batch-item {
      border-top: 1px solid var(--border);
      margin-top: 6px; padding-top: 6px;
    }
    .batch-item-head {
      display: grid; grid-template-columns: 40px 1fr auto;
      gap: 8px; align-items: baseline;
    }
    .batch-item-detail { margin-top: 2px; padding-left: 48px; }

    /* Runs toggle */
    .runs-toggle {
      text-align: center; padding: 10px 0 2px;
      border-top: 1px dashed var(--border);
      margin-top: 8px;
    }

    /* === Now running === */
    .bar {
      flex: 1 1 auto; height: 8px;
      background: var(--bar-bg); border-radius: 4px; overflow: hidden;
    }
    .bar > span {
      display: block; height: 100%;
      background: var(--accent);
      transition: width 0.3s ease;
    }
    .bar.yellow > span { background: var(--yellow); }
    .bar.red    > span { background: var(--red); }
    .bar.green  > span { background: var(--green); }

    /* === Lane cards (multi-lane Now Running) === */
    .lanes-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 12px;
    }
    .lane-card {
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
    }
    .lanes-grid .lane-card { background: var(--panel); }

    /* === Stage-aware progress for the active run === */
    /*
     * Layout: a continuous rail behind the dots, an animated fill that
     * advances with completed stages, and dots that sit on top with
     * state-aware styling. Labels and timings live in a separate row
     * below so layout doesn't shift when retry/attempt info appears.
     */
    .stage-bar {
      position: relative;
      margin: 14px 4px 6px;
      padding: 14px 0 22px;     /* room for labels under the dots */
    }
    .stage-bar .rail {
      position: absolute;
      left: 6px; right: 6px;
      top: 20px; height: 3px;
      background: var(--border);
      border-radius: 2px; overflow: hidden;
    }
    .stage-bar .rail-fill {
      position: absolute; inset: 0;
      width: 0%;
      background: linear-gradient(90deg, var(--green), var(--accent));
      border-radius: 2px;
      transition: width 0.45s cubic-bezier(.22,.61,.36,1);
    }
    .stage-bar .dots {
      position: relative;
      display: flex; justify-content: space-between;
      z-index: 1;
    }
    .stage-bar .seg {
      flex: 0 0 auto;
      display: flex; flex-direction: column;
      align-items: center; gap: 6px;
      width: 14px;
      position: relative;
    }
    .stage-bar .dot {
      width: 14px; height: 14px;
      border-radius: 50%;
      background: var(--panel);
      border: 2px solid var(--border);
      box-shadow: 0 0 0 3px var(--panel);  /* hide rail behind dot */
      transition: background 0.25s ease, border-color 0.25s ease,
                  transform 0.25s cubic-bezier(.22,.61,.36,1);
    }
    .stage-bar .label {
      font-size: 10px;
      color: var(--muted-2);
      text-transform: lowercase;
      letter-spacing: 0.02em;
      white-space: nowrap;
      position: absolute;
      top: 22px;
      left: 50%; transform: translateX(-50%);
      transition: color 0.25s ease, font-weight 0.25s ease;
    }
    .stage-bar .seg.done .dot {
      background: var(--green);
      border-color: var(--green);
    }
    .stage-bar .seg.done .label { color: var(--muted); }
    .stage-bar .seg.active .dot {
      background: var(--accent);
      border-color: var(--accent);
      transform: scale(1.35);
    }
    .stage-bar .seg.active .dot::after {
      content: "";
      position: absolute; inset: -8px;
      border-radius: 50%;
      background: var(--accent);
      opacity: 0.18;
      animation: stagehalo 1.6s ease-in-out infinite;
    }
    .stage-bar .seg.active .label {
      color: var(--text); font-weight: 600;
    }
    .stage-bar .seg.failed .dot {
      background: var(--red); border-color: var(--red);
    }
    /* Retry badge anchored on the dot itself — no layout shift below. */
    .stage-bar .seg .retry-badge {
      position: absolute;
      top: -8px; left: calc(50% + 6px);
      background: var(--yellow);
      color: #0b0c0e;
      font-size: 9px; font-weight: 700;
      padding: 1px 5px; border-radius: 8px;
      line-height: 1.3;
      box-shadow: 0 0 0 2px var(--panel);
      pointer-events: none;
    }
    @keyframes stagehalo {
      0%, 100% { transform: scale(1);   opacity: 0.18; }
      50%      { transform: scale(1.35); opacity: 0;    }
    }
    .stage-meta {
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: baseline;
      gap: 12px;
      margin-top: 4px;
      font-size: 11px;
    }
    .stage-meta .left  { color: var(--text); font-weight: 500; }
    .stage-meta .right {
      color: var(--muted); font-variant-numeric: tabular-nums;
    }

    /* === Modal === */
    .modal-backdrop {
      position: fixed; inset: 0;
      background: var(--backdrop);
      display: none; align-items: center; justify-content: center;
      z-index: 100;
      animation: fade 0.15s ease;
    }
    .modal-backdrop.open { display: flex; }
    .modal {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      box-shadow: var(--shadow);
      width: min(620px, 92vw);
      max-height: 86vh; overflow: auto;
    }
    .modal-head {
      display: flex; align-items: center; justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
    }
    .modal-head h3 { margin: 0; font-size: 15px; font-weight: 600; }
    .modal-body { padding: 16px 20px; }
    .modal-foot {
      display: flex; align-items: center; gap: 10px;
      padding: 14px 20px;
      border-top: 1px solid var(--border);
    }
    .modal-foot .right { margin-left: auto; display: flex; gap: 8px; }
    @keyframes fade { from { opacity: 0; } to { opacity: 1; } }

    fieldset {
      border: 1px solid var(--border); border-radius: 8px;
      padding: 12px 14px 14px; margin: 0 0 12px;
    }
    fieldset legend {
      padding: 0 6px;
      font-size: 10px; font-weight: 700;
      letter-spacing: 0.5px; text-transform: uppercase;
      color: var(--muted);
    }
    fieldset label.block { display: block; margin-top: 10px; }
    fieldset label.block > span {
      display: block; font-size: 11px; color: var(--muted); margin-bottom: 3px;
    }
    fieldset label.block > input,
    fieldset label.block > select { width: 100%; }
    fieldset label.check {
      display: flex; align-items: center; gap: 8px;
      margin-top: 10px; cursor: pointer;
      font-size: 12px;
    }
    fieldset label.check input { accent-color: var(--accent); }
    .seg {
      display: inline-flex; border: 1px solid var(--border);
      border-radius: 6px; overflow: hidden;
      margin-top: 4px;
    }
    .seg button {
      border-radius: 0; border: none;
      background: transparent;
      padding: 5px 12px;
      color: var(--muted);
      font-size: 12px;
    }
    .seg button.active {
      background: var(--accent); color: var(--accent-fg);
    }
    .seg button + button { border-left: 1px solid var(--border); }
    .seg button.active + button { border-left-color: var(--accent); }

    /* === Tooltip === */
    .tt {
      position: fixed; z-index: 200;
      background: var(--panel); border: 1px solid var(--border-2);
      border-radius: 6px; padding: 8px 10px;
      box-shadow: var(--shadow);
      font-size: 11px; color: var(--text);
      pointer-events: none;
      max-width: 280px;
      opacity: 0; transition: opacity 0.1s;
    }
    .tt.show { opacity: 1; }
    .tt .label { color: var(--muted-2); font-size: 10px;
                 text-transform: uppercase; letter-spacing: 0.4px; }
    .tt .row2 { display: flex; gap: 8px; font-variant-numeric: tabular-nums; }
  </style>
</head>
<body>
  <nav class="nav">
    <div class="nav-inner">
      <h1>agents-orchestrator</h1>
      <span class="repo" id="repo">…</span>
      <div class="pills" id="pills"></div>
      <button class="icon-btn" id="theme-btn" title="Toggle theme" onclick="cycleTheme()">◐</button>
      <button class="icon-btn" id="settings-btn" title="Settings" onclick="openSettings()">⚙</button>
    </div>
  </nav>

  <main class="wrap">
    <div class="section" id="now-section">
      <h2>Now running</h2>
      <div id="now"></div>
    </div>

    <div class="section">
      <h2>Run stats</h2>
      <div class="stat-grid" id="stats"></div>
      <div id="chart"></div>
      <div class="chart-meta">
        <span id="chart-meta-left"></span>
        <span id="chart-meta-right"></span>
      </div>
    </div>

    <div class="section">
      <h2>Queue</h2>
      <div id="queue"></div>
    </div>

    <div class="section">
      <h2>Recent runs</h2>
      <div id="runs"></div>
    </div>
  </main>

  <!-- Settings modal -->
  <div class="modal-backdrop" id="modal" onclick="if(event.target===this)closeSettings()">
    <div class="modal" role="dialog" aria-label="Settings">
      <div class="modal-head">
        <h3>Settings</h3>
        <button class="link" onclick="closeSettings()">close</button>
      </div>
      <div class="modal-body">
        <form id="settings-form" onsubmit="return saveSettings(event)">
          <fieldset>
            <legend>Appearance</legend>
            <label class="block">
              <span>Theme</span>
              <div class="seg" id="theme-seg">
                <button type="button" data-theme="system" onclick="setTheme('system')">System</button>
                <button type="button" data-theme="dark" onclick="setTheme('dark')">Dark</button>
                <button type="button" data-theme="light" onclick="setTheme('light')">Light</button>
              </div>
            </label>
          </fieldset>

          <fieldset>
            <legend>Pipeline</legend>
            <label class="block">
              <span>Stop after</span>
              <select name="pipeline.stop_after">
                <option value="pr">pr — open PR, don't merge</option>
                <option value="review">review — PR + auto-review</option>
                <option value="merge">merge — full pipeline</option>
              </select>
            </label>
            <label class="check">
              <input type="checkbox" name="pipeline.auto_review">
              <span>Auto-review before merge</span>
            </label>
          </fieldset>

          <fieldset>
            <legend>Account</legend>
            <label class="block">
              <span>Claude config dir</span>
              <select name="claude.config_dir_preset" onchange="onAcctChange(this)">
                <option value="~/.claude">~/.claude (default / work)</option>
                <option value="~/.claude-personal">~/.claude-personal</option>
                <option value="__custom__">Custom path…</option>
              </select>
            </label>
            <input type="text" name="claude.config_dir" class="custom-path" placeholder="/abs/path or ~/.claude-foo" style="display:none;margin-top:8px">
          </fieldset>

          <fieldset>
            <legend>Run caps</legend>
            <label class="block">
              <span>Wall-clock cap (minutes per claude invocation)</span>
              <input type="number" name="run.cap_minutes" min="1">
            </label>
            <label class="block">
              <span>Retries on gate failure</span>
              <input type="number" name="run.max_retries" min="0">
            </label>
            <label class="block">
              <span>Parallel lanes <span class="small dim">(burst mode only · independent batches run concurrently · 1–4)</span></span>
              <input type="number" name="burst.parallel_lanes" min="1" max="4">
            </label>
          </fieldset>

          <fieldset>
            <legend>Advanced</legend>
            <label class="block">
              <span>Cost-calibration multiplier <span class="small dim">(1.0 = use API rates as-is)</span></span>
              <input type="number" name="claude.cost_calibration" min="0.001" step="0.001">
            </label>
            <div class="small dim" style="margin-top:8px">
              Gates, labels, batch settings and burst caps are edited in
              <code>.agents-orchestrator.toml</code> directly.
            </div>
          </fieldset>
        </form>
      </div>
      <div class="modal-foot">
        <span id="settings-status" class="small dim"></span>
        <div class="right">
          <button onclick="closeSettings()">Cancel</button>
          <button class="primary" onclick="document.getElementById('settings-form').requestSubmit()">Save</button>
        </div>
      </div>
    </div>
  </div>

  <div id="tt" class="tt"></div>

<script>
// ===========================================================================
// Theme
// ===========================================================================
const THEME_KEY = 'ao-theme';
function applyTheme(t) {
  const root = document.documentElement;
  if (t === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', t);
  // reflect in modal segment, if open
  document.querySelectorAll('#theme-seg button').forEach(b => {
    b.classList.toggle('active', b.dataset.theme === t);
  });
  const btn = document.getElementById('theme-btn');
  btn.textContent = t === 'light' ? '☀' : t === 'dark' ? '☾' : '◐';
}
function setTheme(t) {
  localStorage.setItem(THEME_KEY, t);
  applyTheme(t);
}
function cycleTheme() {
  const cur = localStorage.getItem(THEME_KEY) || 'system';
  const next = cur === 'system' ? 'dark' : cur === 'dark' ? 'light' : 'system';
  setTheme(next);
}
applyTheme(localStorage.getItem(THEME_KEY) || 'system');
window.setTheme = setTheme;
window.cycleTheme = cycleTheme;

// ===========================================================================
// Formatters
// ===========================================================================
const $ = sel => document.querySelector(sel);

function fmtTokens(n) {
  n = +n || 0;
  if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1) + 'B';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}
function fmtMoney(n) {
  n = +n || 0;
  return '$' + n.toFixed(n >= 100 ? 0 : 2);
}
function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(sec));
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return m ? `${h}h ${m}m` : `${h}h`;
}
function fmtAgo(iso) {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return '';
  return fmtDuration((Date.now() - t) / 1000) + ' ago';
}
function fmtRunUsage(u) {
  if (!u) return '';
  const inp = +u.input_tokens || 0, out = +u.output_tokens || 0;
  const cost = +u.total_cost_usd || 0;
  if (!inp && !out && !cost) return '';
  return `${fmtTokens(inp)} in / ${fmtTokens(out)} out / $${cost.toFixed(2)}`;
}
function escape(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// ===========================================================================
// Stat tiles + bar chart
// ===========================================================================
function renderStats(state) {
  const s = state.run_stats || { count: 0, tokens_total: 0, tokens_avg: 0, cost_total: 0, cost_avg: 0 };
  const today = state.usage && state.usage.today;
  const todayLine = today && today.ok
    ? `<div class="sub">Today: ${fmtMoney(today.cost_usd)} · ${fmtTokens(today.total_tokens)}</div>`
    : '';
  return `
    <div class="stat">
      <div class="label">Total runs</div>
      <div class="value">${s.count}</div>
      <div class="sub">${state.queue ? state.queue.length : 0} in queue</div>
    </div>
    <div class="stat">
      <div class="label">Tokens</div>
      <div class="value">${fmtTokens(s.tokens_total)}</div>
      <div class="sub">avg ${fmtTokens(s.tokens_avg)} / run</div>
    </div>
    <div class="stat">
      <div class="label">Cost (API estimate)</div>
      <div class="value">${fmtMoney(s.cost_total)}</div>
      <div class="sub">avg ${fmtMoney(s.cost_avg)} / run</div>
    </div>
    <div class="stat">
      <div class="label">Recent</div>
      <div class="value">${state.usage && state.usage.last_30_days && state.usage.last_30_days.ok
          ? fmtMoney(state.usage.last_30_days.cost_usd) : '—'}</div>
      <div class="sub">last 30 days${todayLine ? '' : ''}</div>
      ${todayLine}
    </div>
  `;
}

// Chart filter state — persists across refreshes via in-memory window state.
window.__chartFilters = window.__chartFilters || {
  outcome: 'all',  // 'all' | 'done' | 'failed' | 'needs-info' | 'stopped' | 'finish'
  mode:    'all',  // 'all' | 'burst' | 'slow' | 'finish'
  range:   'all',  // 'all' | '24h' | '7d'  | '30d'
};

const OUTCOME_FILTERS = [
  ['all',        'All'],
  ['done',       'Done'],
  ['failed',     'Failed'],
  ['needs-info', 'Needs-info'],
  ['stopped',    'Stopped'],
];
const MODE_FILTERS = [
  ['all',    'All'],
  ['burst',  'Burst'],
  ['slow',   'Slow'],
  ['finish', 'Finish'],
];
const RANGE_FILTERS = [
  ['all', 'All'],
  ['24h', '24h'],
  ['7d',  '7d'],
  ['30d', '30d'],
];
const RANGE_MS = { '24h': 86400000, '7d': 7 * 86400000, '30d': 30 * 86400000 };

function applyChartFilters(data) {
  const f = window.__chartFilters;
  const now = Date.now();
  return data.filter(p => {
    if (f.outcome !== 'all') {
      const o = String(p.outcome || '');
      if (f.outcome === 'stopped') {
        if (!o.startsWith('stopped') && o !== 'dry-run') return false;
      } else if (o !== f.outcome) {
        return false;
      }
    }
    if (f.mode !== 'all' && String(p.mode || '') !== f.mode) return false;
    if (f.range !== 'all') {
      const ts = p.started_at ? new Date(p.started_at).getTime() : 0;
      if (!ts || now - ts > RANGE_MS[f.range]) return false;
    }
    return true;
  });
}

function setChartFilter(group, value) {
  window.__chartFilters[group] = value;
  renderChart(window.__lastState || {});
}
window.setChartFilter = setChartFilter;

function renderChartFilters() {
  const f = window.__chartFilters;
  const group = (name, key, items) => `
    <div class="filter-group">
      <span class="filter-label">${name}</span>
      ${items.map(([v, label]) => `
        <button type="button" class="chip ${f[key] === v ? 'active' : ''}"
                onclick="setChartFilter('${key}', '${v}')">${escape(label)}</button>
      `).join('')}
    </div>`;
  return `
    <div class="chart-filters">
      ${group('Outcome', 'outcome', OUTCOME_FILTERS)}
      ${group('Mode',    'mode',    MODE_FILTERS)}
      ${group('Range',   'range',   RANGE_FILTERS)}
    </div>`;
}

function renderChart(state) {
  const all = (state.run_stats && state.run_stats.chart) || [];
  const data = applyChartFilters(all);
  const filtersHtml = renderChartFilters();

  if (!all.length) {
    $('#chart').innerHTML = filtersHtml +
      '<div class="chart-empty">No runs yet — kick one off with <code>make slow</code>.</div>';
    $('#chart-meta-left').textContent = '';
    $('#chart-meta-right').textContent = '';
    return;
  }
  if (!data.length) {
    $('#chart').innerHTML = filtersHtml +
      '<div class="chart-empty">No runs match the current filters.</div>';
    $('#chart-meta-left').textContent = `0 of ${all.length} runs`;
    $('#chart-meta-right').textContent = '';
    window.__chartData = [];
    return;
  }

  const max = data.reduce((m, p) => Math.max(m, p.tokens), 0) || 1;
  // Show the issue # under every bar when 18 or fewer bars are visible — at
  // higher density the labels would overlap each other. Above that, fall
  // back to "every Nth" labels.
  const showEveryNth = data.length <= 18 ? 1 : Math.ceil(data.length / 18);
  const bars = data.map((p, i) => {
    const h = Math.max(2, Math.round((p.tokens / max) * 100));
    const cls = 'outcome-' + String(p.outcome || '').replace(/[^a-z-]/g, '-');
    const showLabel = (i % showEveryNth === 0) || i === data.length - 1;
    const label = showLabel ? `<div class="bar-label">#${p.issue_number}</div>` : '';
    return `<div class="bar-col" data-idx="${i}"
              title="#${p.issue_number} · ${escape(p.outcome || '')}"
              onmouseenter="showTip(event, ${i})"
              onmousemove="moveTip(event)"
              onmouseleave="hideTip()"
              onclick="onChartBarClick(${i})">
              <div class="bar-fill ${cls}" style="height:${h}%"></div>
              ${label}
            </div>`;
  }).join('');
  $('#chart').innerHTML = filtersHtml + `<div class="chart">${bars}</div>`;
  $('#chart-meta-left').textContent =
    data.length === all.length
      ? `${data.length} runs`
      : `${data.length} of ${all.length} runs (filtered)`;
  $('#chart-meta-right').textContent =
    `${fmtTokens(data[0].tokens)} → ${fmtTokens(data[data.length - 1].tokens)} (oldest → newest)`;
  window.__chartData = data;
}

function onChartBarClick(idx) {
  const p = (window.__chartData || [])[idx];
  if (!p) return;
  // Click → open log if we have one; otherwise copy the resume command.
  if (p.log_path) {
    openLog(p.log_path, null);
  } else if (p.session_id) {
    copyResume(p.session_id, null);
  }
}
window.onChartBarClick = onChartBarClick;

function showTip(ev, idx) {
  const data = window.__chartData || [];
  const p = data[idx]; if (!p) return;
  const tt = $('#tt');
  const batch = p.batch_id ? ` · batch:${escape(p.batch_id)}` : '';
  const lane  = p.lane    ? ` · lane ${p.lane}`              : '';
  const mode  = p.mode    ? `<span>${escape(p.mode)}</span>` : '';
  const sid   = p.session_id
    ? `<div class="dim mono small">${escape(String(p.session_id).slice(0, 8))}…</div>` : '';
  const pr    = p.pr_url
    ? `<div class="small"><a href="${escape(p.pr_url)}" target="_blank" rel="noopener">open PR</a></div>` : '';
  const hint  = p.log_path
    ? `<div class="dim small">click bar to open log</div>`
    : (p.session_id ? `<div class="dim small">click bar to copy resume cmd</div>` : '');
  tt.innerHTML = `
    <div class="label">Run · ${escape(p.outcome || '')}${lane}</div>
    <div><strong>#${p.issue_number}</strong>${batch}</div>
    <div class="truncate">${escape(p.issue_title || '')}</div>
    <div class="row2 dim">
      <span>${fmtTokens(p.tokens)} tok</span>
      <span>$${(p.cost_usd || 0).toFixed(2)}</span>
      <span>${fmtDuration(p.duration_sec)}</span>
      ${mode}
    </div>
    ${sid}
    ${pr}
    <div class="dim">${fmtAgo(p.started_at)}</div>
    ${hint}
  `;
  tt.classList.add('show');
  moveTip(ev);
}
function moveTip(ev) {
  const tt = $('#tt');
  const x = ev.clientX + 14, y = ev.clientY + 14;
  const w = tt.offsetWidth, h = tt.offsetHeight;
  const maxX = window.innerWidth - w - 10, maxY = window.innerHeight - h - 10;
  tt.style.left = Math.min(x, maxX) + 'px';
  tt.style.top  = Math.min(y, maxY) + 'px';
}
function hideTip() { $('#tt').classList.remove('show'); }
window.showTip = showTip; window.moveTip = moveTip; window.hideTip = hideTip;

// ===========================================================================
// Now running — stage-aware
// ===========================================================================

// Stage lists per run-mode + stop_after. Each entry: [internal-name, label].
const PIPELINE_STAGES_FULL = [
  ['setup',  'setup'],
  ['agent',  'agent'],
  ['gates',  'gates'],
  ['push',   'push'],
  ['pr',     'pr'],
  ['review', 'review'],
  ['merge',  'merge'],
];
const FINISH_STAGES = [
  ['finish-check',  'check'],
  ['finish-rebase', 'rebase'],
  ['finish-push',   'push'],
  ['finish-merge',  'merge'],
];

function stagesFor(state, c) {
  if (c.mode === 'finish') return FINISH_STAGES;
  const stop = state.stop_after || 'merge';
  if (stop === 'pr')     return PIPELINE_STAGES_FULL.slice(0, 5);
  if (stop === 'review') return PIPELINE_STAGES_FULL.slice(0, 6);
  return PIPELINE_STAGES_FULL;
}

function renderStageBar(state, c) {
  const stages = stagesFor(state, c);
  const cur = c.stage || stages[0][0];
  const idx = stages.findIndex(([key]) => key === cur);
  const known = idx >= 0;
  const total = stages.length;

  // Rail fill: 0% at first stage, 100% when last stage is active/done.
  // Position the fill so the rail visually shows progress between dots.
  let fillPct = 0;
  if (known && total > 1) {
    fillPct = Math.min(100, (idx / (total - 1)) * 100);
  }

  // Show the retry badge on the dot that's actively being retried.
  // For pipeline runs, retries happen during agent or gates stages.
  const retryStages = new Set(['agent', 'gates']);
  const attempt = (c.attempt && c.attempt > 0) ? c.attempt : 0;

  const dots = stages.map(([key, label], i) => {
    let cls = 'pending';
    if (known) {
      if (i < idx) cls = 'done';
      else if (i === idx) cls = 'active';
    }
    const badge = (attempt && cls === 'active' && retryStages.has(key))
      ? `<span class="retry-badge" title="Retry ${attempt}">${attempt}</span>` : '';
    return `<div class="seg ${cls}"><div class="dot">${badge}</div><div class="label">${label}</div></div>`;
  }).join('');

  return `
    <div class="stage-bar">
      <div class="rail"><div class="rail-fill" style="width:${fillPct}%"></div></div>
      <div class="dots">${dots}</div>
    </div>
  `;
}

// Inline SVG icons — `clipboard` for the resume-line copy button, `file`
// for "view log". Kept tiny so they live in two compact icon buttons.
const ICON_COPY = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="9" height="10" rx="1.5"/><path d="M3 11V2.5A1.5 1.5 0 0 1 4.5 1H10"/></svg>`;
const ICON_CHECK = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.5 3.5L13 5"/></svg>`;
const ICON_LOG = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 2h7l3 3v9H3z"/><path d="M10 2v3h3"/><path d="M5 8h6M5 11h6"/></svg>`;

function iconCopyBtn(sid) {
  return `<button class="icon-btn" title="Copy: claude --resume ${sid}" aria-label="Copy resume command" onclick="copyResume('${sid}', this)">${ICON_COPY}</button>`;
}
function iconLogBtn(logPath) {
  return `<button class="icon-btn" title="Open run log" aria-label="Open run log" onclick="openLog('${logPath}', this)">${ICON_LOG}</button>`;
}

function renderNow(state) {
  // Multi-lane support: state.currents is an array of lane state objects.
  // Fall back to the legacy single state.current for old payloads.
  const currents = (Array.isArray(state.currents) && state.currents.length)
    ? state.currents
    : (state.current ? [state.current] : []);
  if (!currents.length) {
    const note = (state.parallel_lanes > 1)
      ? `<div class="empty">— idle (${state.parallel_lanes} lanes ready)</div>`
      : '<div class="empty">— none</div>';
    return note;
  }
  if (currents.length === 1) {
    return renderLaneCard(state, currents[0], /*showLanePill=*/false);
  }
  return `<div class="lanes-grid">` +
    currents.map(c => renderLaneCard(state, c, /*showLanePill=*/true)).join('') +
    `</div>`;
}

function renderLaneCard(state, c, showLanePill) {
  const startedTs   = c.started_at ? new Date(c.started_at).getTime() : null;
  const stageStarts = c.stage_started_at ? new Date(c.stage_started_at).getTime() : startedTs;
  const elapsedTotal = startedTs ? (Date.now() - startedTs) / 1000 : 0;
  const elapsedStage = stageStarts ? (Date.now() - stageStarts) / 1000 : 0;
  const cap = (c.cap_minutes || state.current_cap_minutes || 30) * 60;

  const batchPill = c.batch_id
    ? `<span class="pill purple">batch:${escape(c.batch_id)}</span>` : '';
  const modePill = c.mode === 'finish'
    ? `<span class="pill yellow">finish</span>` : '';
  const lanePill = showLanePill
    ? `<span class="pill accent">lane ${c.lane || 1}</span>` : '';
  const sid = escape(c.session_id || '');
  const logPath = escape(c.log_path || '');

  const stageBar = renderStageBar(state, c);
  const stageLabel = escape(c.stage || '?');
  // The retry badge sits on the active dot — no need to repeat it in the
  // meta line. Keep meta short.
  const attemptHtml = '';

  const sessionRow = sid ? `
    <div class="session-row">
      <span class="mono small truncate">claude --resume ${sid}</span>
      ${iconCopyBtn(sid)}
      ${logPath ? iconLogBtn(logPath) : ''}
    </div>` : (logPath ? `
    <div class="session-row">
      <span class="mono small dim truncate">no agent session — orchestrator op</span>
      ${iconLogBtn(logPath)}
    </div>` : `
    <div class="session-row">
      <span class="mono small dim truncate">no agent session — orchestrator op</span>
    </div>`);

  return `
    <div class="lane-card">
      <div class="grid3">
        <div><strong>#${c.issue_number}</strong>${batchPill}${modePill}${lanePill}</div>
        <div class="truncate">${escape(c.issue_title || '')}</div>
        <div class="small mono">${fmtDuration(elapsedTotal)} / ${fmtDuration(cap)}</div>
      </div>
      ${stageBar}
      <div class="stage-meta">
        <div class="left">${stageLabel}${attemptHtml}</div>
        <div class="right">${fmtDuration(elapsedStage)} in stage</div>
      </div>
      ${sessionRow}
    </div>
  `;
}

// ===========================================================================
// Queue
// ===========================================================================
function renderQueue(state) {
  if (state.queue_error) {
    return `<div class="empty">could not read queue: ${escape(state.queue_error)}</div>`;
  }
  const groups = state.queue_groups || [];
  if (!groups.length) return '<div class="empty">— empty</div>';
  let html = '';
  for (const g of groups) {
    const head = g.batch_id
      ? `<div class="small" style="margin-top:6px"><span class="pill purple">batch:${escape(g.batch_id)}</span></div>` : '';
    if (head) html += head;
    for (const iss of g.issues) {
      const age = fmtAgo(iss.createdAt).replace(' ago', '');
      html += `
        <div class="row">
          <div class="small mono" style="min-width:36px">#${iss.number}</div>
          <div class="truncate" style="flex:1">${escape(iss.title)}</div>
          <div class="small dim">${age}</div>
        </div>`;
    }
  }
  return html;
}

// ===========================================================================
// Recent runs
// ===========================================================================
function renderRuns(state) {
  const allRuns = state.runs || [];
  if (!allRuns.length) return '<div class="empty">— none yet</div>';

  const showAll = window.__showAllRuns === true;
  let runs;
  if (showAll) {
    runs = allRuns;
  } else {
    const seen = new Set();
    runs = [];
    for (const r of allRuns) {
      if (seen.has(r.issue_number)) continue;
      seen.add(r.issue_number);
      runs.push(r);
    }
  }

  const hiddenCount = allRuns.length - runs.length;
  const toggle = (hiddenCount > 0 || showAll) ? `
    <div class="runs-toggle small dim">
      <button class="link" onclick="toggleAllRuns()" type="button">
        ${showAll ? '↑ Hide history — show only latest per issue' : `↓ Show full history (${hiddenCount} older run${hiddenCount === 1 ? '' : 's'} hidden)`}
      </button>
    </div>` : '';

  const groups = [];
  let cur = null;
  for (const r of runs) {
    if (r.batch_id && cur
        && cur.session_id === r.session_id
        && cur.batch_id === r.batch_id) {
      cur.runs.push(r);
    } else {
      cur = { session_id: r.session_id, batch_id: r.batch_id, runs: [r] };
      groups.push(cur);
    }
  }
  return groups.map(g => g.runs.length > 1 ? renderBatchGroup(g) : renderSingleRun(g.runs[0])).join('') + toggle;
}

function renderSingleRun(r) {
  const outcome = r.outcome || '?';
  const cls = outcome.replace(/[^a-z]/g, '-');
  const dur = fmtDuration(r.duration_sec || 0);
  const usage = fmtRunUsage(r.usage);
  const pr = r.pr_url ? ` · <a href="${escape(r.pr_url)}" target="_blank">PR</a>` : '';
  const batchPill = r.batch_id
    ? `<span class="pill purple">batch:${escape(r.batch_id)}</span>` : '';
  const sid = escape(r.session_id || '');
  const logPath = escape(r.log_path || '');
  return `
    <div class="row" style="display:block">
      <div class="grid3">
        <div class="mono">#${r.issue_number}</div>
        <div class="truncate">${escape(r.issue_title || '')} ${batchPill}</div>
        <div class="small dim">${dur}</div>
      </div>
      <div class="grid3" style="margin-top:4px">
        <div class="outcome ${cls} small">${escape(outcome)}</div>
        <div class="small truncate">${escape(r.detail || '')}${pr}</div>
        <div class="small dim">${escape(usage)}</div>
      </div>
      ${sid ? `
      <div class="session-row">
        <span class="mono truncate">claude --resume ${sid}</span>
        ${iconCopyBtn(sid)}
        ${logPath ? iconLogBtn(logPath) : ''}
      </div>` : ''}
    </div>
  `;
}

function renderBatchGroup(g) {
  const items = [...g.runs].reverse();
  const agg = items.reduce((a, r) => {
    const u = r.usage || {};
    a.inp += +u.input_tokens || 0;
    a.out += +u.output_tokens || 0;
    a.cost += +u.total_cost_usd || 0;
    a.dur += +r.duration_sec || 0;
    return a;
  }, { inp: 0, out: 0, cost: 0, dur: 0 });
  const tail = items[items.length - 1];
  const sid = escape(tail.session_id || '');
  const logPath = escape(tail.log_path || '');
  const issueNums = items.map(r => '#' + r.issue_number).join(', ');
  const itemRows = items.map(r => {
    const outcome = r.outcome || '?';
    const cls = outcome.replace(/[^a-z]/g, '-');
    const pr = r.pr_url ? ` · <a href="${escape(r.pr_url)}" target="_blank">PR</a>` : '';
    const u = fmtRunUsage(r.usage);
    return `
      <div class="batch-item">
        <div class="batch-item-head">
          <span class="mono">#${r.issue_number}</span>
          <span class="truncate">${escape(r.issue_title || '')}</span>
          <span class="outcome ${cls} small">${escape(outcome)}</span>
        </div>
        <div class="batch-item-detail small dim">
          ${escape(r.detail || '')}${pr}
          ${u ? `<span style="float:right">${escape(u)}</span>` : ''}
        </div>
      </div>`;
  }).join('');

  return `
    <div class="row" style="display:block">
      <div class="grid3">
        <div>
          <span class="pill purple">batch:${escape(g.batch_id)}</span>
          <span class="small dim" style="margin-left:6px">${items.length} issues · ${issueNums}</span>
        </div>
        <div></div>
        <div class="small dim">${fmtDuration(agg.dur)}</div>
      </div>
      <div class="batch-list">${itemRows}</div>
      <div class="grid3" style="margin-top:6px">
        <div class="small dim">batch totals</div>
        <div></div>
        <div class="small dim">${fmtTokens(agg.inp)} in / ${fmtTokens(agg.out)} out / $${agg.cost.toFixed(2)}</div>
      </div>
      ${sid ? `
      <div class="session-row">
        <span class="mono truncate">claude --resume ${sid}</span>
        ${iconCopyBtn(sid)}
        ${logPath ? iconLogBtn(logPath) : ''}
      </div>` : ''}
    </div>
  `;
}

function toggleAllRuns() {
  window.__showAllRuns = !(window.__showAllRuns === true);
  refresh();
}
window.toggleAllRuns = toggleAllRuns;

// ===========================================================================
// Actions
// ===========================================================================
async function copyResume(sid, btn) {
  try {
    const state = window.__lastState || {};
    const prefix = state.resume_env_prefix || '';
    const repo = state.repo_root || '';
    const cdPart = repo ? `cd '${repo}' && ` : '';
    const cmd = `${cdPart}${prefix}claude --resume ${sid}`;
    await navigator.clipboard.writeText(cmd);
    flashIcon(btn);
  } catch (e) { alert('Clipboard write failed: ' + e.message); }
}
window.copyResume = copyResume;

async function openLog(path, btn) {
  try {
    const r = await fetch('/api/open_log', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { alert('Could not open log: ' + (body.error || r.status)); return; }
    flashIcon(btn);
  } catch (e) { alert('Could not open log: ' + e.message); }
}
window.openLog = openLog;

// Flash a check-mark inside an icon-btn for ~1s then revert.
function flashIcon(btn) {
  if (!btn) return;
  const original = btn.innerHTML;
  btn.classList.add('copied');
  btn.innerHTML = ICON_CHECK;
  setTimeout(() => {
    btn.classList.remove('copied');
    btn.innerHTML = original;
  }, 900);
}

// ===========================================================================
// Settings modal — dirty-tracking edition
//
// Only fields the user actually touches are sent on Save. Without this, a
// Save click before state loaded (or with state pre-populated but untouched)
// would push the form's *current display* — including HTML default values
// — back to the server, silently downgrading any real toml settings.
//
// Two safety nets:
//   1. data-dirty attribute set via input/change events; save reads only
//      dirty fields.
//   2. Save button disabled until /api/state has populated __lastState.
// ===========================================================================

function openSettings() {
  syncSettingsForm(window.__lastState || {});
  document.getElementById('modal').classList.add('open');
  resetDirty();
  updateSaveEnabled();
}
function closeSettings() {
  document.getElementById('modal').classList.remove('open');
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeSettings();
});
window.openSettings = openSettings; window.closeSettings = closeSettings;

function _allFormFields() {
  return document.querySelectorAll('#settings-form [name]');
}
function resetDirty() {
  _allFormFields().forEach(el => el.removeAttribute('data-dirty'));
  const status = document.getElementById('settings-status');
  if (status) { status.textContent = ''; status.style.color = ''; }
}
function markDirty(el) {
  if (el && el.name) el.setAttribute('data-dirty', '1');
}
function updateSaveEnabled() {
  const ready = window.__lastState && window.__lastState.settings;
  const btn = document.querySelector('.modal-foot button.primary');
  if (btn) {
    btn.disabled = !ready;
    btn.title = ready ? '' : 'Waiting for state to load…';
  }
}

function onAcctChange(sel) {
  const custom = document.querySelector('.custom-path');
  if (sel.value === '__custom__') {
    custom.style.display = '';
    custom.focus();
  } else {
    custom.style.display = 'none';
    custom.value = sel.value;
  }
  // Programmatic .value changes don't fire input/change events, so mark
  // the destination field dirty explicitly when the preset moves it.
  markDirty(custom);
}
window.onAcctChange = onAcctChange;

function syncSettingsForm(state) {
  const f = document.getElementById('settings-form');
  if (!f) return;
  const s = state.settings || {};
  for (const [k, v] of Object.entries(s)) {
    const el = f.querySelector(`[name="${k}"]`);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = !!v;
    else if (v == null) el.value = '';
    else el.value = v;
  }
  const dir = s['claude.config_dir'] || '';
  const preset = f.querySelector('[name="claude.config_dir_preset"]');
  const custom = f.querySelector('.custom-path');
  const known = ['~/.claude', '~/.claude-personal'];
  const norm = dir.replace(/^\/Users\/[^/]+\//, '~/');
  if (known.includes(norm)) {
    preset.value = norm; custom.style.display = 'none'; custom.value = norm;
  } else {
    preset.value = '__custom__'; custom.style.display = ''; custom.value = dir;
  }
  // Re-wire dirty listeners once per form lifetime.
  if (!f._dirtyWired) {
    f.addEventListener('input',  e => markDirty(e.target));
    f.addEventListener('change', e => markDirty(e.target));
    f._dirtyWired = true;
  }
}

async function saveSettings(ev) {
  ev.preventDefault();
  const f = document.getElementById('settings-form');
  const status = document.getElementById('settings-status');
  if (!window.__lastState || !window.__lastState.settings) {
    status.textContent = 'state not loaded yet — try again in a sec';
    status.style.color = 'var(--yellow)';
    return false;
  }

  // Collect only dirty fields. claude.config_dir_preset is a UI affordance,
  // not a real toml key — strip it; the real value is in claude.config_dir.
  const payload = {};
  for (const el of _allFormFields()) {
    if (!el.hasAttribute('data-dirty')) continue;
    if (el.name === 'claude.config_dir_preset') continue;
    let val;
    if (el.type === 'checkbox') {
      val = el.checked;
    } else if (el.type === 'number') {
      const raw = el.value;
      val = raw === '' ? null : Number(raw);
    } else if (el.type === 'datetime-local') {
      const raw = (el.value || '').trim();
      val = raw === '' ? null : raw;
    } else {
      const raw = (el.value || '').trim();
      val = raw === '' ? null : raw;
    }
    payload[el.name] = val;
  }

  if (Object.keys(payload).length === 0) {
    status.textContent = 'no changes';
    status.style.color = 'var(--muted)';
    setTimeout(() => { closeSettings(); status.textContent = ''; }, 500);
    return false;
  }

  status.textContent = 'saving ' + Object.keys(payload).length + ' field' +
                      (Object.keys(payload).length === 1 ? '' : 's') + '…';
  status.style.color = '';
  try {
    const r = await fetch('/api/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      status.textContent = 'error: ' + (body.error || r.status);
      status.style.color = 'var(--red)';
      return false;
    }
    status.textContent = `saved ${body.changed} field${body.changed === 1 ? '' : 's'}`;
    status.style.color = 'var(--green)';
    resetDirty();
    setTimeout(() => { closeSettings(); status.textContent = ''; }, 700);
    refresh();
  } catch (e) {
    status.textContent = 'error: ' + e.message;
    status.style.color = 'var(--red)';
  }
  return false;
}
window.saveSettings = saveSettings;

// ===========================================================================
// Main refresh loop — ETag-gated polling.
//
// Server returns 304 when state hasn't changed; we skip the re-render in
// that case. With 304s being cheap, we can poll faster (1.5s) without
// burning CPU or re-rendering on every tick. The "ticker" interval at
// 250ms re-renders the Now card to keep the elapsed counter moving even
// when state is unchanged.
// ===========================================================================
let lastError = null;
let lastEtag = null;

function applyState(state) {
  window.__lastState = state;

  $('#repo').textContent = state.repo + ' → ' + state.base_branch;

  const pills = [];
  if (state.claude_account) pills.push(`<span class="pill accent">${escape(state.claude_account)}</span>`);
  pills.push(`<span class="pill">${escape(state.stop_after || 'merge')}</span>`);
  if (state.auto_review) pills.push('<span class="pill green">auto-review</span>');
  if (state.dry_run) pills.push('<span class="pill yellow">dry-run</span>');
  if ((state.parallel_lanes || 1) > 1) {
    pills.push(`<span class="pill purple">${state.parallel_lanes}× lanes</span>`);
  }
  $('#pills').innerHTML = pills.join('');

  $('#now').innerHTML = renderNow(state);
  $('#stats').innerHTML = renderStats(state);
  renderChart(state);
  $('#queue').innerHTML = renderQueue(state);
  $('#runs').innerHTML = renderRuns(state);

  if (document.getElementById('modal').classList.contains('open')) {
    updateSaveEnabled();
  }
}

async function refresh() {
  try {
    const headers = {};
    if (lastEtag) headers['If-None-Match'] = lastEtag;
    const r = await fetch('/api/state', { cache: 'no-store', headers });
    if (r.status === 304) { lastError = null; return; }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const state = await r.json();
    if (state.error) throw new Error(state.error);
    lastEtag = r.headers.get('ETag');
    applyState(state);
    lastError = null;
  } catch (e) {
    if (lastError !== e.message) {
      console.warn('state refresh failed:', e);
      lastError = e.message;
    }
  }
}

// Lightweight ticker — re-renders the "Now running" card from cached state
// at 4 Hz so the elapsed counter and progress bar advance smoothly without
// any server round-trip.
function tickNow() {
  const st = window.__lastState;
  if (!st || !st.current) return;
  $('#now').innerHTML = renderNow(st);
}

refresh();
setInterval(refresh, 1500);
setInterval(tickNow, 250);
</script>
</body>
</html>
"""


def serve(port: int = 7777) -> int:
    """Start the cockpit server. Blocks until Ctrl-C."""
    cfg = config.load()
    # Touch the config eagerly so a bad TOML fails fast, not on first request.
    addr = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(addr, _Handler)
    print(f"[ao-web] {cfg.repo} — http://127.0.0.1:{port}", flush=True)
    print("[ao-web] Ctrl-C to stop.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[ao-web] stopping…", flush=True)
    finally:
        httpd.server_close()
    return 0
