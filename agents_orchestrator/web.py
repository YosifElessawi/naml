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

    session = claude_session.session_usage()
    weekly = claude_session.weekly_usage()
    today = claude_session.today_usage()
    last30 = claude_session.thirty_day_usage()

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
            "session": _usage_to_dict(session, limit=cfg.session_token_limit),
            "weekly": _usage_to_dict(weekly, limit=cfg.weekly_token_limit),
            "today": _usage_to_dict(today),
            "last_30_days": _usage_to_dict(last30),
            "limits_configured": bool(cfg.session_token_limit or cfg.weekly_token_limit),
            "burst_min_tokens": cfg.burst_min_tokens,
        },
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
    :root {
      --bg: #0e0e10;
      --panel: #18181b;
      --panel-2: #1f1f23;
      --border: #2a2a2f;
      --text: #e6e6ea;
      --muted: #8a8a92;
      --muted-2: #5b5b62;
      --accent: #58a6ff;
      --green: #3fb950;
      --yellow: #d29922;
      --red: #f85149;
      --purple: #bc8cff;
      --bar-bg: #2a2a2f;
      --bar-fill: #ec8a5d;   /* CodexBar coral for the usage bars */
      --bar-reserve: #6b3c2a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 13px/1.55 -apple-system, BlinkMacSystemFont, "SF Pro Text",
            "Segoe UI", system-ui, sans-serif;
    }
    .wrap { max-width: 880px; margin: 0 auto; padding: 24px 20px 64px; }
    header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 22px; }
    header h1 {
      font-size: 15px; font-weight: 600; margin: 0; letter-spacing: 0.2px;
      text-transform: uppercase; color: var(--accent);
    }
    header .repo { color: var(--muted); font-size: 13px; }
    header .meta { margin-left: auto; color: var(--muted); font-size: 12px; }
    .section {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 14px;
    }
    .section h2 {
      font-size: 11px; font-weight: 600; letter-spacing: 0.5px;
      text-transform: uppercase; color: var(--muted);
      margin: 0 0 10px 0;
    }
    /* CodexBar-style usage row: big label, big bar, % left + reset on a
       single subtitle line. */
    .usage-row { margin: 14px 0 18px; }
    .usage-row:first-child { margin-top: 4px; }
    .usage-row .title {
      font-size: 14px; font-weight: 600; color: var(--text);
      margin-bottom: 8px;
    }
    .usage-row .subtitle {
      display: flex; justify-content: space-between; align-items: baseline;
      margin-top: 6px; font-size: 12px; color: var(--muted);
      font-variant-numeric: tabular-nums;
    }
    .usage-row .subtitle .right { color: var(--muted-2); }
    .ubar {
      height: 10px; width: 100%;
      background: var(--bar-bg); border-radius: 5px; overflow: hidden;
      position: relative;
    }
    .ubar > span {
      display: block; height: 100%;
      background: var(--bar-fill);
      transition: width 0.3s ease;
    }
    .ubar.unconfigured > span {
      background: var(--muted-2);
    }
    .row { display: flex; align-items: center; gap: 10px; padding: 6px 0; }
    .row + .row { border-top: 1px solid var(--border); }
    .row .label { flex: 0 0 auto; min-width: 60px; color: var(--muted); }
    .row .body { flex: 1 1 auto; }
    .row .right { flex: 0 0 auto; color: var(--muted); font-variant-numeric: tabular-nums; }
    .bar {
      flex: 1 1 auto;
      height: 8px;
      background: var(--panel-2);
      border-radius: 4px;
      overflow: hidden;
      position: relative;
    }
    .bar > span {
      display: block; height: 100%;
      background: var(--accent);
      transition: width 0.3s ease;
    }
    .bar.green > span { background: var(--green); }
    .bar.yellow > span { background: var(--yellow); }
    .bar.red > span { background: var(--red); }
    .cost-row {
      display: flex; justify-content: space-between;
      padding: 10px 0; font-size: 12px;
    }
    .cost-row .left { color: var(--muted); }
    .cost-row .right { color: var(--text); font-variant-numeric: tabular-nums; }
    .cost-row + .cost-row { border-top: 1px solid var(--border); }
    .config-hint {
      font-size: 11px; color: var(--muted-2);
      padding: 8px 10px;
      background: rgba(255,255,255,0.02);
      border: 1px dashed var(--border);
      border-radius: 6px;
      margin-top: 6px;
    }
    .config-hint code { color: var(--accent); background: transparent; padding: 0; }
    .config-hint .calib {
      display: flex; gap: 10px; flex-wrap: wrap;
      align-items: center; margin-top: 8px;
    }
    .config-hint .calib label {
      display: inline-flex; align-items: center; gap: 4px;
      color: var(--muted); font-size: 11px;
    }
    .config-hint .calib input {
      width: 64px; padding: 3px 5px;
      background: var(--bg); color: var(--text);
      border: 1px solid var(--border); border-radius: 4px;
      font: inherit; font-size: 11px;
    }
    /* Calibrate panel inside the Usage section */
    .calib-wrap { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--border); }
    button.link {
      background: transparent; border: none; color: var(--accent);
      font-size: 11px; padding: 0; cursor: pointer;
    }
    button.link:hover { text-decoration: underline; }
    .calib-body { margin-top: 8px; padding: 10px 12px;
      background: rgba(255,255,255,0.02); border: 1px solid var(--border);
      border-radius: 6px;
    }
    .calib-grid {
      display: grid; grid-template-columns: 1fr 1fr;
      gap: 8px 12px;
    }
    .calib-grid label {
      display: block; font-size: 11px; color: var(--muted);
    }
    .calib-grid input {
      display: block; margin-top: 3px; width: 100%;
      padding: 4px 6px;
      background: var(--bg); color: var(--text);
      border: 1px solid var(--border); border-radius: 4px;
      font: inherit; font-size: 12px;
    }
    .calib-actions {
      display: flex; align-items: center; gap: 10px;
      margin-top: 10px;
    }
    .calib-actions button {
      padding: 4px 10px; font-size: 11px;
    }
    /* Batched recent-run grouping */
    .batch-list {
      margin: 8px 0 4px;
      padding: 8px 10px 4px;
      background: rgba(255,255,255,0.025);
      border-left: 2px solid var(--purple);
      border-radius: 4px;
    }
    .batch-item + .batch-item {
      border-top: 1px solid var(--border);
      margin-top: 6px; padding-top: 6px;
    }
    .batch-item-head {
      display: grid;
      grid-template-columns: 36px 1fr auto;
      gap: 8px; align-items: baseline;
    }
    .batch-item-detail {
      margin-top: 2px; padding-left: 44px;
      overflow: hidden;
    }
    .runs-toggle {
      text-align: center; padding: 10px 0 2px;
      border-top: 1px dashed var(--border);
      margin-top: 8px;
    }
    /* Settings form */
    .section h2 .hint {
      text-transform: none; letter-spacing: 0;
      font-weight: 400; font-size: 11px;
      color: var(--muted-2); margin-left: 8px;
    }
    .settings-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }
    fieldset {
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px 12px 12px;
      margin: 0;
      background: rgba(255,255,255,0.015);
    }
    legend {
      padding: 0 6px;
      font-size: 10px; font-weight: 600;
      letter-spacing: 0.5px; text-transform: uppercase;
      color: var(--muted);
    }
    fieldset label.block { display: block; margin: 8px 0 0; }
    fieldset label.block > span {
      display: block; font-size: 11px; color: var(--muted);
      margin-bottom: 3px;
    }
    fieldset label.check {
      display: flex; align-items: center; gap: 6px;
      font-size: 12px; color: var(--text); margin: 10px 0 0;
      cursor: pointer;
    }
    fieldset label.check input { accent-color: var(--accent); }
    fieldset input[type="number"],
    fieldset input[type="text"],
    fieldset select {
      width: 100%;
      padding: 5px 8px;
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 5px;
      font: inherit; font-size: 12px;
      font-variant-numeric: tabular-nums;
    }
    fieldset select:focus, fieldset input:focus {
      outline: none; border-color: var(--accent);
    }
    .custom-path { margin-top: 6px; }
    .settings-actions {
      display: flex; align-items: center; gap: 12px;
      padding-top: 6px;
    }
    .settings-actions button {
      padding: 6px 14px; font-size: 12px; font-weight: 500;
      background: var(--accent); color: #0d1117;
      border: 1px solid var(--accent);
    }
    .settings-actions button:hover { filter: brightness(1.1); }
    .dim { color: var(--muted-2); }
    .pill {
      display: inline-block;
      padding: 1px 8px;
      border-radius: 10px;
      background: var(--panel-2);
      border: 1px solid var(--border);
      color: var(--muted);
      font-size: 11px;
      margin-left: 6px;
    }
    .pill.green { color: var(--green); border-color: rgba(63, 185, 80, 0.3); }
    .pill.red { color: var(--red); border-color: rgba(248, 81, 73, 0.3); }
    .pill.yellow { color: var(--yellow); border-color: rgba(210, 153, 34, 0.3); }
    .pill.purple { color: var(--purple); border-color: rgba(188, 140, 255, 0.3); }
    .pill.accent { color: var(--accent); border-color: rgba(88, 166, 255, 0.3); }
    button {
      background: var(--panel-2);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 3px 9px;
      font: 11px/1.4 inherit;
      cursor: pointer;
    }
    button:hover { background: #222a33; border-color: #444c56; }
    .empty { color: var(--muted); font-style: italic; padding: 4px 0; }
    .mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
    .small { font-size: 11px; color: var(--muted); }
    .truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .grid2 { display: grid; grid-template-columns: 1fr auto; gap: 8px 16px; align-items: center; }
    .grid3 { display: grid; grid-template-columns: auto 1fr auto; gap: 8px 12px; align-items: center; }
    .session-row { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
    .session-row .mono { flex: 1; min-width: 0; }
    .outcome { font-weight: 600; min-width: 110px; }
    .outcome.done { color: var(--green); }
    .outcome.failed { color: var(--red); }
    .outcome.stopped-at-pr, .outcome.stopped-at-review { color: var(--yellow); }
    .outcome.needs-info { color: var(--accent); }
    .outcome.dry-run { color: var(--yellow); }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>agents-orchestrator</h1>
      <span class="repo mono" id="repo">…</span>
      <span class="meta" id="meta"></span>
    </header>

    <div class="section" id="now-section">
      <h2>Now running</h2>
      <div id="now"></div>
    </div>

    <div class="section">
      <h2>Usage</h2>
      <div id="usage"></div>
    </div>

    <div class="section">
      <h2>Cost</h2>
      <div id="cost"></div>
    </div>

    <div class="section">
      <h2>Queue</h2>
      <div id="queue"></div>
    </div>

    <div class="section">
      <h2>Recent runs</h2>
      <div id="runs"></div>
    </div>

    <div class="section">
      <h2>Settings <span class="hint">writes to .agents-orchestrator.toml</span></h2>
      <form id="settings-form" onsubmit="return saveSettings(event)">
        <div class="settings-grid">
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
            <input class="custom-path" type="text" name="claude.config_dir" placeholder="/abs/path or ~/.claude-foo" style="display:none">
          </fieldset>

          <!-- Plan caps + reset anchors moved out of the everyday form;
               surfaced via the "Calibrate from /usage" panel inside the
               Usage section. The cockpit works without them — the bars
               just go to raw-tokens mode. -->


          <fieldset>
            <legend>Cost</legend>
            <label class="block">
              <span>Calibration multiplier</span>
              <input type="number" name="claude.cost_calibration" min="0.001" step="0.001">
            </label>
            <div class="small dim">1.0 = use baked-in prices as-is.</div>
          </fieldset>

          <fieldset>
            <legend>Run caps</legend>
            <label class="block">
              <span>Cap (minutes)</span>
              <input type="number" name="run.cap_minutes" min="1">
            </label>
            <label class="block">
              <span>Retries on gate failure</span>
              <input type="number" name="run.max_retries" min="0">
            </label>
          </fieldset>
        </div>

        <div class="settings-actions">
          <button type="submit">Save changes</button>
          <span id="settings-status" class="small dim"></span>
          <span class="small dim" style="margin-left:auto">
            Gates, labels, batch and burst caps live in the TOML file.
          </span>
        </div>
      </form>
    </div>
  </div>

<script>
const $ = sel => document.querySelector(sel);

function fmtTokens(n) {
  n = +n || 0;
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return n.toString();
}

function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(sec));
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return m ? `${h}h ${m}m` : `${h}h`;
}

function fmtAgo(iso) {
  if (!iso) return 'unknown';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return 'unknown';
  return fmtDuration((Date.now() - t) / 1000) + ' ago';
}

function fmtUsage(u) {
  if (!u) return '';
  const inp = +u.input_tokens || 0, out = +u.output_tokens || 0;
  const cost = +u.total_cost_usd || 0;
  if (!inp && !out && !cost) return '';
  return `${fmtTokens(inp)} in / ${fmtTokens(out)} out / $${cost.toFixed(2)}`;
}

function bar(pct, klass = '') {
  pct = Math.max(0, Math.min(100, pct));
  return `<div class="bar ${klass}"><span style="width:${pct}%"></span></div>`;
}

function escape(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

async function openAgent(sid, btn) {
  try {
    const r = await fetch('/api/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert('Could not open agent: ' + (body.error || r.status));
      return;
    }
    flashOK(btn, 'Opened');
  } catch (e) {
    alert('Could not open agent: ' + e.message);
  }
}
window.openAgent = openAgent;

async function copyResume(sid, btn) {
  // Mirror the server-side _claude_env logic via the resume_env_prefix
  // the server pre-computes (empty when using the default ~/.claude
  // account, since adding an explicit env var would break the keychain
  // lookup).
  try {
    const state = window.__lastState || {};
    const prefix = state.resume_env_prefix || '';
    const repo = state.repo_root || '';
    const cdPart = repo ? `cd '${repo}' && ` : '';
    const cmd = `${cdPart}${prefix}claude --resume ${sid}`;
    await navigator.clipboard.writeText(cmd);
    flashOK(btn, 'Copied');
  } catch (e) {
    alert('Clipboard write failed: ' + e.message);
  }
}
window.copyResume = copyResume;

async function openLog(path, btn) {
  try {
    const r = await fetch('/api/open_log', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert('Could not open log: ' + (body.error || r.status));
      return;
    }
    flashOK(btn, 'Opened');
  } catch (e) {
    alert('Could not open log: ' + e.message);
  }
}
window.openLog = openLog;

function flashOK(btn, msg) {
  if (!btn) return;
  const original = btn.textContent;
  btn.textContent = msg;
  btn.disabled = true;
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1100);
}

function renderNow(state) {
  const c = state.current;
  if (!c) {
    return '<div class="empty">— none</div>';
  }
  const startedTs = c.started_at ? new Date(c.started_at).getTime() : null;
  const elapsed = startedTs ? (Date.now() - startedTs) / 1000 : 0;
  const cap = (c.cap_minutes || state.current_cap_minutes || 30) * 60;
  const pct = cap ? Math.round(100 * elapsed / cap) : 0;
  const klass = pct > 80 ? 'red' : (pct > 60 ? 'yellow' : 'green');
  const batchPill = c.batch_id
    ? `<span class="pill purple">batch:${escape(c.batch_id)}</span>` : '';
  return `
    <div class="grid3">
      <div><strong>#${c.issue_number}</strong>${batchPill}</div>
      <div class="truncate">${escape(c.issue_title || '')}</div>
      <div class="small mono">${fmtDuration(elapsed)} / ${fmtDuration(cap)}</div>
    </div>
    <div style="margin-top:8px">${bar(pct, klass)}</div>
    <div class="session-row">
      <span class="mono small truncate">claude --resume ${escape(c.session_id || '')}</span>
      <button onclick="openAgent('${escape(c.session_id || '')}', this)">Open agent</button>
      <button onclick="copyResume('${escape(c.session_id || '')}', this)">Copy</button>
      ${c.log_path ? `<button onclick="openLog('${escape(c.log_path)}', this)">View log</button>` : ''}
    </div>
  `;
}

function usageRow(title, u, opts = {}) {
  if (!u || !u.ok) {
    return `
      <div class="usage-row">
        <div class="title">${title}</div>
        <div class="ubar unconfigured"><span style="width:0%"></span></div>
        <div class="subtitle"><span>no local transcripts in this window</span><span></span></div>
      </div>
    `;
  }
  const reset = u.reset_in_seconds != null ? `resets in ${fmtDuration(u.reset_in_seconds)}` : '';
  if (u.limit && u.pct_used != null) {
    const pctLeft = Math.max(0, 100 - u.pct_used);
    const fillPct = Math.min(100, u.pct_used);
    return `
      <div class="usage-row">
        <div class="title">${title}</div>
        <div class="ubar"><span style="width:${fillPct}%"></span></div>
        <div class="subtitle">
          <span><strong>${pctLeft.toFixed(0)}%</strong> left
                <span class="right" style="margin-left:8px">${fmtTokens(u.total_tokens)} of ${fmtTokens(u.limit)} tokens</span></span>
          <span class="right">${reset}</span>
        </div>
      </div>
    `;
  }
  // No cap configured — show raw tokens, no fill (we don't know the denominator).
  return `
    <div class="usage-row">
      <div class="title">${title}</div>
      <div class="ubar unconfigured"><span style="width:0%"></span></div>
      <div class="subtitle">
        <span>${fmtTokens(u.total_tokens)} tokens · ~$${(u.cost_usd || 0).toFixed(2)} <span class="right" style="margin-left:6px">(API retail est.)</span></span>
        <span class="right">${reset}</span>
      </div>
    </div>
  `;
}

function renderUsage(state) {
  const u = state.usage || {};
  let out = '';
  out += usageRow('Session (5h rolling)', u.session);
  out += usageRow('Weekly (7d rolling)', u.weekly);

  // Calibration panel — collapsed by default. Only opens when the user
  // explicitly wants to sync caps + reset anchors from Claude's /usage.
  const sessTok = u.session && u.session.ok ? u.session.total_tokens : 0;
  const weekTok = u.weekly && u.weekly.ok ? u.weekly.total_tokens : 0;
  const haveLimits = u.limits_configured;
  out += `
    <div class="calib-wrap">
      <button class="link" onclick="toggleCalib(this)" type="button">
        ${haveLimits ? '⚙ Recalibrate from /usage' : '⚙ Calibrate from /usage'}
      </button>
      <div class="calib-body" style="display:none">
        <div class="small dim" style="margin-bottom:6px">
          Open Claude Code → Settings → Usage. Read the % numbers and
          the next reset times, paste them here. Both the cap and the
          reset anchor get saved — one-time per account.
        </div>
        <div class="calib-grid">
          <label>Session %
            <input id="calib-s" type="number" min="0" max="100" step="0.1" placeholder="e.g. 85"/>
          </label>
          <label>Weekly %
            <input id="calib-w" type="number" min="0" max="100" step="0.1" placeholder="e.g. 23"/>
          </label>
          <label>Session reset
            <input id="calib-sr" type="datetime-local"/>
          </label>
          <label>Weekly reset
            <input id="calib-wr" type="datetime-local"/>
          </label>
        </div>
        <div class="calib-actions">
          <button onclick="applyCalibration(${sessTok}, ${weekTok}, this)" type="button">Save</button>
          <span id="calib-status" class="small dim"></span>
        </div>
      </div>
    </div>
  `;
  return out;
}

function toggleCalib(btn) {
  const body = btn.nextElementSibling;
  body.style.display = body.style.display === 'none' ? '' : 'none';
}
window.toggleCalib = toggleCalib;

async function applyCalibration(sessTok, weekTok, btn) {
  const sPct = parseFloat(document.getElementById('calib-s').value);
  const wPct = parseFloat(document.getElementById('calib-w').value);
  const sReset = document.getElementById('calib-sr').value;
  const wReset = document.getElementById('calib-wr').value;
  const status = document.getElementById('calib-status');

  function backSolve(used, pct) {
    if (!used || !pct || pct <= 0 || pct > 100) return null;
    return Math.ceil((used / (pct / 100)) / 1_000_000) * 1_000_000;
  }

  const payload = {};
  const sCap = backSolve(sessTok, sPct);
  const wCap = backSolve(weekTok, wPct);
  if (sCap) payload['claude.session_token_limit'] = sCap;
  if (wCap) payload['claude.weekly_token_limit'] = wCap;
  if (sReset) payload['claude.session_reset_at'] = sReset;
  if (wReset) payload['claude.weekly_reset_at'] = wReset;

  if (Object.keys(payload).length === 0) {
    status.textContent = 'fill in at least one value';
    status.style.color = 'var(--yellow)';
    return;
  }

  status.textContent = 'saving…';
  status.style.color = 'var(--muted)';
  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      status.textContent = 'error: ' + (body.error || r.status);
      status.style.color = 'var(--red)';
      return;
    }
    status.textContent = `saved ${body.changed} field${body.changed === 1 ? '' : 's'}`;
    status.style.color = 'var(--green)';
    setTimeout(() => { status.textContent = ''; }, 3000);
    refresh();
  } catch (e) {
    status.textContent = 'error: ' + e.message;
    status.style.color = 'var(--red)';
  }
}
window.applyCalibration = applyCalibration;

function calibrate(sessTok, weekTok) {
  const s = parseFloat(document.getElementById('calib-s').value);
  const w = parseFloat(document.getElementById('calib-w').value);
  const lines = ['[claude]'];
  function backSolve(used, pct) {
    if (!used || !pct || pct <= 0 || pct > 100) return null;
    const raw = used / (pct / 100);
    // Round up to the next 1M.
    return Math.ceil(raw / 1_000_000) * 1_000_000;
  }
  function fmt(n) {
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, '_');
  }
  const sCap = backSolve(sessTok, s);
  const wCap = backSolve(weekTok, w);
  if (!sCap && !wCap) {
    alert('Enter at least one valid % (0 < pct ≤ 100).');
    return;
  }
  if (sCap) lines.push(`session_token_limit = ${fmt(sCap)}`);
  if (wCap) lines.push(`weekly_token_limit  = ${fmt(wCap)}`);
  lines.push('');
  lines.push('# Paste this into .agents-orchestrator.toml under [claude],');
  lines.push('# then reload this page.');
  const el = document.getElementById('calib-out');
  el.textContent = lines.join('\n');
  el.style.display = 'block';
}
window.calibrate = calibrate;

// --- Settings form ---------------------------------------------------------

let _settingsDirty = false;
const FORM = () => document.getElementById('settings-form');

function onAcctChange(sel) {
  const custom = FORM().querySelector('.custom-path');
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
  // Don't clobber edits in progress.
  if (_settingsDirty) return;
  const f = FORM();
  if (!f) return;
  const s = state.settings || {};
  for (const [k, v] of Object.entries(s)) {
    const el = f.querySelector(`[name="${k}"]`);
    if (!el) continue;
    if (el.type === 'checkbox') {
      el.checked = !!v;
    } else if (v == null) {
      el.value = '';
    } else if (el.type === 'datetime-local') {
      // datetime-local needs `YYYY-MM-DDTHH:MM` in LOCAL time (no tz).
      try {
        const d = new Date(v);
        if (!isNaN(d.getTime())) {
          const pad = n => String(n).padStart(2, '0');
          el.value = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
        } else {
          el.value = '';
        }
      } catch (e) {
        el.value = '';
      }
    } else {
      el.value = v;
    }
  }
  // Account selector: preset vs custom.
  const dir = s['claude.config_dir'] || '';
  const preset = f.querySelector('[name="claude.config_dir_preset"]');
  const custom = f.querySelector('.custom-path');
  const known = ['~/.claude', '~/.claude-personal'];
  const normalized = dir.replace(/\/Users\/[^/]+\//, '~/');
  if (known.includes(normalized)) {
    preset.value = normalized;
    custom.style.display = 'none';
    custom.value = normalized;
  } else {
    preset.value = '__custom__';
    custom.style.display = '';
    custom.value = dir;
  }
  // Once we've populated, listen for any change to mark dirty.
  if (!f._wired) {
    f.addEventListener('input', () => { _settingsDirty = true; });
    f.addEventListener('change', () => { _settingsDirty = true; });
    f._wired = true;
  }
}

async function saveSettings(ev) {
  ev.preventDefault();
  const f = FORM();
  const status = document.getElementById('settings-status');
  const data = new FormData(f);
  const payload = {};

  // Convert form fields into the wire format.
  const stop = data.get('pipeline.stop_after');
  if (stop) payload['pipeline.stop_after'] = stop;
  payload['pipeline.auto_review'] = f.querySelector('[name="pipeline.auto_review"]').checked;

  // Account: use custom field's value (preset onchange syncs it).
  const dir = (data.get('claude.config_dir') || '').trim();
  if (dir) payload['claude.config_dir'] = dir;

  for (const key of ['claude.session_token_limit', 'claude.weekly_token_limit',
                     'claude.cost_calibration', 'run.cap_minutes', 'run.max_retries']) {
    const raw = data.get(key);
    payload[key] = raw === '' || raw == null ? null : Number(raw);
  }
  for (const key of ['claude.session_reset_at', 'claude.weekly_reset_at']) {
    const raw = (data.get(key) || '').trim();
    payload[key] = raw === '' ? null : raw;
  }

  status.textContent = 'saving…';
  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      status.textContent = 'error: ' + (body.error || r.status);
      status.style.color = 'var(--red)';
      return false;
    }
    status.textContent = `saved (${body.changed} field${body.changed === 1 ? '' : 's'})`;
    status.style.color = 'var(--green)';
    _settingsDirty = false;
    setTimeout(() => { status.textContent = ''; status.style.color = ''; }, 3000);
    refresh();
  } catch (e) {
    status.textContent = 'error: ' + e.message;
    status.style.color = 'var(--red)';
  }
  return false;
}
window.saveSettings = saveSettings;

function renderCost(state) {
  const u = state.usage || {};
  const today = u.today || {};
  const last30 = u.last_30_days || {};
  function tokens(x) {
    return x && x.ok ? fmtTokens(x.total_tokens) : '—';
  }
  function money(x) {
    return x && x.ok ? `$${(x.cost_usd || 0).toFixed(2)}` : '—';
  }
  return `
    <div class="cost-row">
      <span class="left">Today</span>
      <span class="right">${money(today)} · ${tokens(today)} tokens</span>
    </div>
    <div class="cost-row">
      <span class="left">Last 30 days</span>
      <span class="right">${money(last30)} · ${tokens(last30)} tokens</span>
    </div>
    <div class="config-hint" style="margin-top:10px">
      Estimated at Anthropic's published API rates. Your actual Pro/Max bill
      is fixed by subscription — these numbers tell you what the same work
      would cost on the API.
    </div>
  `;
}

function renderQueue(state) {
  if (state.queue_error) {
    return `<div class="empty">could not read queue: ${escape(state.queue_error)}</div>`;
  }
  const groups = state.queue_groups || [];
  if (!groups.length) return '<div class="empty">— empty</div>';
  let html = '';
  for (const g of groups) {
    const head = g.batch_id
      ? `<div class="small"><span class="pill purple">batch:${escape(g.batch_id)}</span></div>`
      : '';
    if (head) html += head;
    for (const iss of g.issues) {
      const age = fmtAgo(iss.createdAt).replace(' ago', '');
      html += `
        <div class="row">
          <div class="label mono">#${iss.number}</div>
          <div class="body truncate">${escape(iss.title)}</div>
          <div class="right small">${age}</div>
        </div>
      `;
    }
  }
  return html;
}

function renderRuns(state) {
  const allRuns = state.runs || [];
  if (!allRuns.length) return '<div class="empty">— none yet</div>';

  // Dedupe (unless toggled): keep only the most recent run per
  // issue_number. Old failed/needs-info attempts get suppressed once a
  // newer outcome lands. The JSONL ledger keeps full history; this is
  // purely a UI filter so the cockpit reads as "current state".
  const showAll = window.__showAllRuns === true;
  let runs;
  if (showAll) {
    runs = allRuns;
  } else {
    const seen = new Set();
    runs = [];
    for (const r of allRuns) {           // newest-first already
      if (seen.has(r.issue_number)) continue;
      seen.add(r.issue_number);
      runs.push(r);
    }
  }

  const hiddenCount = allRuns.length - runs.length;
  const toggle = `
    <div class="runs-toggle small dim">
      ${hiddenCount > 0 || showAll
        ? `<button class="link" onclick="toggleAllRuns()" type="button">
             ${showAll ? '↑ Hide history — show only latest per issue' : `↓ Show full history (${hiddenCount} older run${hiddenCount === 1 ? '' : 's'} hidden)`}
           </button>`
        : ''}
    </div>
  `;

  // Group consecutive runs that share (session_id, batch_id) — those are
  // the issues from one batched session and should render as one card.
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
  return groups.map(g => g.runs.length > 1 ? renderBatchGroup(g) : renderSingleRun(g.runs[0])).join('')
       + toggle;
}

function toggleAllRuns() {
  window.__showAllRuns = !(window.__showAllRuns === true);
  refresh();
}
window.toggleAllRuns = toggleAllRuns;

function renderSingleRun(r) {
  const outcome = r.outcome || '?';
  const cls = outcome.replace(/[^a-z]/g, '-');
  const dur = fmtDuration(r.duration_sec || 0);
  const usage = fmtUsage(r.usage);
  const pr = r.pr_url ? `· <a href="${escape(r.pr_url)}" target="_blank">PR</a>` : '';
  const batchPill = r.batch_id
    ? `<span class="pill purple">batch:${escape(r.batch_id)}</span>` : '';
  return `
    <div class="row" style="display:block">
      <div class="grid3">
        <div class="mono">#${r.issue_number}</div>
        <div class="body truncate">${escape(r.issue_title || '')}${batchPill}</div>
        <div class="right small">${dur}</div>
      </div>
      <div class="grid3" style="margin-top:4px">
        <div class="outcome ${cls}">${escape(outcome)}</div>
        <div class="small truncate">${escape(r.detail || '')} ${pr}</div>
        <div class="right small">${escape(usage)}</div>
      </div>
      ${r.session_id ? `
      <div class="session-row">
        <span class="mono small truncate">claude --resume ${escape(r.session_id)}</span>
        <button onclick="openAgent('${escape(r.session_id)}', this)">Open agent</button>
        <button onclick="copyResume('${escape(r.session_id)}', this)">Copy</button>
        ${r.log_path ? `<button onclick="openLog('${escape(r.log_path)}', this)">View log</button>` : ''}
      </div>` : ''}
    </div>
  `;
}

function renderBatchGroup(g) {
  // g.runs is newest-first within the group (which means highest batch_position
  // first — issue 3, then 2, then 1 for a 3-issue batch). Reorder to natural
  // batch order (ascending position) for clearer reading.
  const items = [...g.runs].reverse();

  // Aggregate token usage + cost across the batch.
  const agg = items.reduce((a, r) => {
    const u = r.usage || {};
    a.inp += +u.input_tokens || 0;
    a.out += +u.output_tokens || 0;
    a.cost += +u.total_cost_usd || 0;
    a.dur += +r.duration_sec || 0;
    return a;
  }, { inp: 0, out: 0, cost: 0, dur: 0 });

  // Pick a shared log to view (the most recent, which is the last one in items).
  const tail = items[items.length - 1];
  const sid = tail.session_id || '';
  const logPath = tail.log_path || '';
  const issueNums = items.map(r => '#' + r.issue_number).join(', ');

  const itemRows = items.map(r => {
    const outcome = r.outcome || '?';
    const cls = outcome.replace(/[^a-z]/g, '-');
    const pr = r.pr_url ? ` · <a href="${escape(r.pr_url)}" target="_blank">PR</a>` : '';
    const u = fmtUsage(r.usage);
    return `
      <div class="batch-item">
        <div class="batch-item-head">
          <span class="mono">#${r.issue_number}</span>
          <span class="truncate body">${escape(r.issue_title || '')}</span>
          <span class="outcome ${cls} small">${escape(outcome)}</span>
        </div>
        <div class="batch-item-detail small dim">
          ${escape(r.detail || '')}${pr}
          ${u ? `<span style="float:right">${escape(u)}</span>` : ''}
        </div>
      </div>
    `;
  }).join('');

  return `
    <div class="row" style="display:block">
      <div class="grid3">
        <div>
          <span class="pill purple">batch:${escape(g.batch_id)}</span>
          <span class="small dim" style="margin-left:6px">${items.length} issues · ${issueNums}</span>
        </div>
        <div></div>
        <div class="right small">${fmtDuration(agg.dur)}</div>
      </div>
      <div class="batch-list">${itemRows}</div>
      <div class="grid3" style="margin-top:6px">
        <div class="small dim">batch totals</div>
        <div></div>
        <div class="right small">${fmtTokens(agg.inp)} in / ${fmtTokens(agg.out)} out / $${agg.cost.toFixed(2)}</div>
      </div>
      ${sid ? `
      <div class="session-row">
        <span class="mono small truncate">claude --resume ${escape(sid)}</span>
        <button onclick="openAgent('${escape(sid)}', this)">Open agent</button>
        <button onclick="copyResume('${escape(sid)}', this)">Copy</button>
        ${logPath ? `<button onclick="openLog('${escape(logPath)}', this)">View latest log</button>` : ''}
      </div>` : ''}
    </div>
  `;
}

let lastError = null;
async function refresh() {
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const state = await r.json();
    if (state.error) throw new Error(state.error);
    window.__lastState = state;
    $('#repo').textContent = state.repo + '  →  ' + state.base_branch;
    // Show the active config knobs unconditionally — easier to spot a
    // surprising setting than to remember which ones are "interesting".
    const flags = [];
    if (state.claude_account) flags.push('account: ' + state.claude_account);
    flags.push('stop_after=' + (state.stop_after || 'merge'));
    flags.push('auto_review=' + (state.auto_review ? 'on' : 'off'));
    if (state.dry_run) flags.push('dry_run');
    $('#meta').textContent = flags.join(' · ');
    $('#now').innerHTML = renderNow(state);
    $('#usage').innerHTML = renderUsage(state);
    $('#cost').innerHTML = renderCost(state);
    $('#queue').innerHTML = renderQueue(state);
    $('#runs').innerHTML = renderRuns(state);
    syncSettingsForm(state);
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
