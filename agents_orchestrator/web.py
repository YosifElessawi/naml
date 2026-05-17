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

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import claude_session, config, github_ops


class _LaunchError(RuntimeError):
    pass


def _open_in_terminal(cwd: str, cmd: str) -> None:
    """Spawn a new terminal window in ``cwd`` running ``cmd``.

    Auto-detects:
      1. cmux  — if its binary is on PATH, use `cmux new-workspace --cwd … --command …`
      2. macOS Terminal — fall back to osascript

    Override via the env var ``AO_TERMINAL_CMD`` (a shell command with
    ``{cwd}`` and ``{cmd}`` placeholders), e.g.::

        export AO_TERMINAL_CMD='wezterm cli spawn --cwd {cwd} -- bash -c "{cmd}"'
    """
    template = os.environ.get("AO_TERMINAL_CMD", "").strip()
    if template:
        rendered = template.replace("{cwd}", cwd).replace("{cmd}", cmd)
        try:
            subprocess.run(rendered, shell=True, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise _LaunchError(f"AO_TERMINAL_CMD failed: {exc.stderr.strip() or exc}")
        return

    cmux = shutil.which("cmux")
    if cmux:
        # cmux opens a new workspace running the command directly — no
        # shell wrapper needed. Command must include the env-var prefix
        # itself (we already format it that way).
        try:
            subprocess.run(
                [cmux, "new-workspace", "--cwd", cwd, "--command", cmd],
                check=True, capture_output=True, text=True,
            )
            return
        except subprocess.CalledProcessError as exc:
            # Fall through to Terminal if cmux is broken.
            sys.stderr.write(f"[ao-web] cmux launch failed, falling back: {exc.stderr.strip()}\n")

    # macOS Terminal fallback via osascript. Build a shell command that
    # cds first so the resumed agent sees the repo as cwd.
    full = f"cd {shlex.quote(cwd)} && {cmd}"
    # Escape backslashes and double-quotes for the AppleScript string literal.
    esc = full.replace("\\", "\\\\").replace('"', '\\"')
    applescript = (
        f'tell application "Terminal" to do script "{esc}"\n'
        'tell application "Terminal" to activate'
    )
    try:
        subprocess.run(
            ["osascript", "-e", applescript],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise _LaunchError(f"failed to open Terminal: {exc.stderr.strip() or exc}")


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
    cfg = config.load()
    if not cfg.current_json.exists():
        return None
    try:
        return json.loads(cfg.current_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


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
    current = _read_current()
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
        },
        "now": datetime.now(timezone.utc).isoformat(),
        "current": current,
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
            self._send_json(payload)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/config":
            self._handle_config_post()
            return
        if self.path == "/api/open_log":
            self._handle_open_log()
            return
        if self.path != "/api/open":
            self._send_json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, status=400)
            return
        session_id = payload.get("session_id", "")
        if not isinstance(session_id, str) or not session_id:
            self._send_json({"error": "session_id required"}, status=400)
            return
        # Guard against shell-injection: only allow UUID-shaped strings.
        import re
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", session_id):
            self._send_json({"error": "session_id must be a UUID-like token"}, status=400)
            return

        # Build the claude command — pin CLAUDE_CONFIG_DIR to the toml's
        # claude.config_dir so the resumed agent uses the SAME account the
        # orchestrator ran it under. EXCEPT when the toml value equals the
        # default ~/.claude — claude's keychain entry for the default
        # account is keyed against the unset env var, not the explicit
        # path. Setting the var explicitly there would surface "Not
        # logged in". Same logic as runner._claude_env.
        cfg = config.load()
        default_dir = Path.home() / ".claude"
        if cfg.claude_config_dir.resolve() == default_dir.resolve():
            cmd = f"claude --resume {session_id}"
        else:
            claude_dir = str(cfg.claude_config_dir)
            cmd = f"CLAUDE_CONFIG_DIR={shlex.quote(claude_dir)} claude --resume {session_id}"
        cwd = str(cfg.repo_root)

        try:
            _open_in_terminal(cwd, cmd)
        except _LaunchError as exc:
            self._send_json({"error": str(exc)}, status=500)
            return
        self._send_json({"ok": True, "cmd": cmd, "cwd": cwd})

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
    .chart {
      display: flex; align-items: stretch;  /* stretch so .bar-col fills height */
      gap: 3px;
      height: 100px;
      padding: 8px 0 0;
      margin-top: 6px;
      border-top: 1px solid var(--border);
    }
    .chart .bar-col {
      flex: 1;
      min-width: 5px;
      max-width: 22px;
      height: 100%;          /* explicit height so .bar-fill % heights resolve */
      display: flex; flex-direction: column;
      justify-content: flex-end;
      cursor: pointer;
    }
    .chart .bar-fill {
      width: 100%;
      background: var(--bar-good);
      border-radius: 2px 2px 0 0;
      transition: opacity 0.12s;
    }
    .chart .bar-col:hover .bar-fill { opacity: 0.7; }
    .chart .bar-fill.outcome-failed     { background: var(--bar-failed); }
    .chart .bar-fill.outcome-needs-info { background: var(--bar-needs); }
    .chart .bar-fill.outcome-stopped-at-pr,
    .chart .bar-fill.outcome-stopped-at-review,
    .chart .bar-fill.outcome-dry-run    { background: var(--yellow); }
    .chart .bar-fill.outcome-done       { background: var(--green); }
    .chart-meta {
      display: flex; justify-content: space-between;
      font-size: 11px; color: var(--muted-2);
      padding: 6px 0 0;
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

function renderChart(state) {
  const data = (state.run_stats && state.run_stats.chart) || [];
  if (!data.length) {
    $('#chart').innerHTML = '<div class="chart-empty">No runs yet — kick one off with <code>make slow</code>.</div>';
    $('#chart-meta-left').textContent = '';
    $('#chart-meta-right').textContent = '';
    return;
  }
  const max = data.reduce((m, p) => Math.max(m, p.tokens), 0) || 1;
  const bars = data.map((p, i) => {
    const h = Math.max(2, Math.round((p.tokens / max) * 100));
    const cls = 'outcome-' + String(p.outcome || '').replace(/[^a-z-]/g, '-');
    return `<div class="bar-col" data-idx="${i}"
              onmouseenter="showTip(event, ${i})"
              onmousemove="moveTip(event)"
              onmouseleave="hideTip()">
              <div class="bar-fill ${cls}" style="height:${h}%"></div>
            </div>`;
  }).join('');
  $('#chart').innerHTML = `<div class="chart">${bars}</div>`;
  $('#chart-meta-left').textContent = `${data.length} runs`;
  $('#chart-meta-right').textContent =
    `${fmtTokens(data[0].tokens)} → ${fmtTokens(data[data.length - 1].tokens)} (oldest → newest)`;
  window.__chartData = data;
}

function showTip(ev, idx) {
  const data = window.__chartData || [];
  const p = data[idx]; if (!p) return;
  const tt = $('#tt');
  const batch = p.batch_id ? ` · batch:${escape(p.batch_id)}` : '';
  tt.innerHTML = `
    <div class="label">Run · ${escape(p.outcome || '')}</div>
    <div><strong>#${p.issue_number}</strong>${batch}</div>
    <div class="truncate">${escape(p.issue_title || '')}</div>
    <div class="row2 dim">
      <span>${fmtTokens(p.tokens)} tok</span>
      <span>$${(p.cost_usd || 0).toFixed(2)}</span>
      <span>${fmtDuration(p.duration_sec)}</span>
    </div>
    <div class="dim">${fmtAgo(p.started_at)}</div>
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
// Now running
// ===========================================================================
function renderNow(state) {
  const c = state.current;
  if (!c) return '<div class="empty">— none</div>';
  const startedTs = c.started_at ? new Date(c.started_at).getTime() : null;
  const elapsed = startedTs ? (Date.now() - startedTs) / 1000 : 0;
  const cap = (c.cap_minutes || state.current_cap_minutes || 30) * 60;
  const pct = cap ? Math.round(100 * elapsed / cap) : 0;
  const klass = pct > 80 ? 'red' : (pct > 60 ? 'yellow' : 'green');
  const batchPill = c.batch_id
    ? `<span class="pill purple">batch:${escape(c.batch_id)}</span>` : '';
  const sid = escape(c.session_id || '');
  const logPath = escape(c.log_path || '');
  return `
    <div class="grid3">
      <div><strong>#${c.issue_number}</strong>${batchPill}</div>
      <div class="truncate">${escape(c.issue_title || '')}</div>
      <div class="small mono">${fmtDuration(elapsed)} / ${fmtDuration(cap)}</div>
    </div>
    <div style="margin-top:8px"><div class="bar ${klass}"><span style="width:${pct}%"></span></div></div>
    <div class="session-row">
      <span class="mono small truncate">claude --resume ${sid}</span>
      <button onclick="openAgent('${sid}', this)">Open agent</button>
      <button onclick="copyResume('${sid}', this)">Copy</button>
      ${logPath ? `<button onclick="openLog('${logPath}', this)">View log</button>` : ''}
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
        <button onclick="openAgent('${sid}', this)">Open agent</button>
        <button onclick="copyResume('${sid}', this)">Copy</button>
        ${logPath ? `<button onclick="openLog('${logPath}', this)">View log</button>` : ''}
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
        <button onclick="openAgent('${sid}', this)">Open agent</button>
        <button onclick="copyResume('${sid}', this)">Copy</button>
        ${logPath ? `<button onclick="openLog('${logPath}', this)">View latest log</button>` : ''}
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
async function openAgent(sid, btn) {
  try {
    const r = await fetch('/api/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { alert('Could not open agent: ' + (body.error || r.status)); return; }
    flashOK(btn, 'Opened');
  } catch (e) { alert('Could not open agent: ' + e.message); }
}
window.openAgent = openAgent;

async function copyResume(sid, btn) {
  try {
    const state = window.__lastState || {};
    const prefix = state.resume_env_prefix || '';
    const repo = state.repo_root || '';
    const cdPart = repo ? `cd '${repo}' && ` : '';
    const cmd = `${cdPart}${prefix}claude --resume ${sid}`;
    await navigator.clipboard.writeText(cmd);
    flashOK(btn, 'Copied');
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
    flashOK(btn, 'Opened');
  } catch (e) { alert('Could not open log: ' + e.message); }
}
window.openLog = openLog;

function flashOK(btn, msg) {
  if (!btn) return;
  const original = btn.textContent;
  btn.textContent = msg;
  btn.disabled = true;
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1000);
}

// ===========================================================================
// Settings modal
// ===========================================================================
let _settingsDirty = false;

function openSettings() {
  syncSettingsForm(window.__lastState || {});
  document.getElementById('modal').classList.add('open');
  _settingsDirty = false;
}
function closeSettings() {
  document.getElementById('modal').classList.remove('open');
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeSettings();
});
window.openSettings = openSettings; window.closeSettings = closeSettings;

function onAcctChange(sel) {
  const custom = document.querySelector('.custom-path');
  if (sel.value === '__custom__') {
    custom.style.display = '';
    custom.focus();
  } else {
    custom.style.display = 'none';
    custom.value = sel.value;
  }
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
}

async function saveSettings(ev) {
  ev.preventDefault();
  const f = document.getElementById('settings-form');
  const status = document.getElementById('settings-status');
  const data = new FormData(f);
  const payload = {};

  const stop = data.get('pipeline.stop_after');
  if (stop) payload['pipeline.stop_after'] = stop;
  payload['pipeline.auto_review'] = f.querySelector('[name="pipeline.auto_review"]').checked;

  const dir = (data.get('claude.config_dir') || '').trim();
  if (dir) payload['claude.config_dir'] = dir;

  for (const key of ['claude.cost_calibration', 'run.cap_minutes', 'run.max_retries']) {
    const raw = data.get(key);
    payload[key] = raw === '' || raw == null ? null : Number(raw);
  }

  status.textContent = 'saving…'; status.style.color = '';
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
    setTimeout(() => { closeSettings(); status.textContent = ''; }, 800);
    refresh();
  } catch (e) {
    status.textContent = 'error: ' + e.message;
    status.style.color = 'var(--red)';
  }
  return false;
}
window.saveSettings = saveSettings;

// ===========================================================================
// Main refresh loop
// ===========================================================================
let lastError = null;
async function refresh() {
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const state = await r.json();
    if (state.error) throw new Error(state.error);
    window.__lastState = state;

    $('#repo').textContent = state.repo + ' → ' + state.base_branch;

    const pills = [];
    if (state.claude_account) pills.push(`<span class="pill accent">${escape(state.claude_account)}</span>`);
    pills.push(`<span class="pill">${escape(state.stop_after || 'merge')}</span>`);
    if (state.auto_review) pills.push('<span class="pill green">auto-review</span>');
    if (state.dry_run) pills.push('<span class="pill yellow">dry-run</span>');
    $('#pills').innerHTML = pills.join('');

    $('#now').innerHTML = renderNow(state);
    $('#stats').innerHTML = renderStats(state);
    renderChart(state);
    $('#queue').innerHTML = renderQueue(state);
    $('#runs').innerHTML = renderRuns(state);

    lastError = null;
  } catch (e) {
    if (lastError !== e.message) {
      console.warn('state refresh failed:', e);
      lastError = e.message;
    }
  }
}

refresh();
setInterval(refresh, 5000);
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
