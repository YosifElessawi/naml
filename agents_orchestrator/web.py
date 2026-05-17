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
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def _build_state() -> dict:
    cfg = config.load()
    current = _read_current()
    runs = _read_runs(limit=20)
    queue, queue_err = _read_queue_cached()
    est = claude_session.estimate()

    # Group queue by batch for the UI.
    queue_by_batch: dict[str | None, list[dict]] = {}
    for issue in queue:
        bid = github_ops.batch_id_of(issue)
        queue_by_batch.setdefault(bid, []).append(issue)
    queue_groups = [
        {"batch_id": bid, "issues": items}
        for bid, items in queue_by_batch.items()
    ]

    return {
        "repo": cfg.repo,
        "base_branch": cfg.base_branch,
        "stop_after": cfg.stop_after,
        "auto_review": cfg.auto_review,
        "dry_run": cfg.dry_run,
        "now": datetime.now(timezone.utc).isoformat(),
        "current": current,
        "current_cap_minutes": cfg.run_cap_minutes,
        "runs": runs,
        "queue": queue,
        "queue_groups": queue_groups,
        "queue_error": queue_err,
        "pro": {
            "used": est.used,
            "limit": est.limit,
            "headroom": est.headroom,
            "reset_in_seconds": est.reset_in_seconds,
            "ok": est.ok,
            "min_headroom": cfg.burst_min_headroom,
        },
    }


# --- HTTP handler ---------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — stdlib signature
        # Quieter logs — only show errors.
        if args and isinstance(args[0], str) and args[0].startswith(("4", "5")):
            sys.stderr.write("[ao-web] " + (format % args) + "\n")

    def do_GET(self) -> None:  # noqa: N802 — stdlib signature
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(_INDEX_HTML)
            return
        if self.path == "/api/state":
            try:
                self._send_json(_build_state())
            except Exception as exc:  # last-ditch — surface to UI
                self._send_json({"error": str(exc)}, status=500)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
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

        # Build the claude command, honoring CLAUDE_CONFIG_DIR if set in the
        # orchestrator's env (so the Terminal inherits the same account).
        import os
        cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
        prefix = f"CLAUDE_CONFIG_DIR={cfg_dir} " if cfg_dir else ""
        cmd = f"{prefix}claude --resume {session_id}"

        # macOS: open a new Terminal window running the command.
        # Use osascript so we don't depend on Terminal's CLI shape.
        applescript = (
            'tell application "Terminal" to do script '
            f'"{cmd.replace(chr(34), chr(92) + chr(34))}"\n'
            'tell application "Terminal" to activate'
        )
        try:
            subprocess.run(
                ["osascript", "-e", applescript],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as exc:
            self._send_json({"error": f"failed to open Terminal: {exc.stderr.strip()}"}, status=500)
            return
        self._send_json({"ok": True})


# --- index.html (embedded) ------------------------------------------------

_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>agents-orchestrator</title>
  <style>
    :root {
      --bg: #0d1117;
      --panel: #161b22;
      --panel-2: #1c232b;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #58a6ff;
      --green: #3fb950;
      --yellow: #d29922;
      --red: #f85149;
      --purple: #bc8cff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 13px/1.5 -apple-system, BlinkMacSystemFont, "SF Pro Text",
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
      <h2>Claude Pro window (5h rolling)</h2>
      <div id="pro"></div>
    </div>

    <div class="section">
      <h2>Queue</h2>
      <div id="queue"></div>
    </div>

    <div class="section">
      <h2>Recent runs</h2>
      <div id="runs"></div>
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

async function openAgent(sid) {
  try {
    const r = await fetch('/api/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ error: 'unknown' }));
      alert('Could not open agent: ' + (err.error || r.status));
    }
  } catch (e) {
    alert('Could not open agent: ' + e.message);
  }
}
window.openAgent = openAgent;

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
      <button onclick="openAgent('${escape(c.session_id || '')}')">Open agent</button>
    </div>
  `;
}

function renderPro(state) {
  const p = state.pro;
  if (!p || !p.ok || p.used == null) {
    return '<div class="empty">estimate unavailable (no local transcripts yet)</div>';
  }
  const pct = Math.round(100 * p.used / p.limit);
  const klass = (p.headroom <= p.min_headroom) ? 'red'
              : (p.headroom <= p.min_headroom * 2) ? 'yellow' : 'green';
  const resetIn = p.reset_in_seconds != null ? fmtDuration(p.reset_in_seconds) : '—';
  return `
    <div class="grid3">
      <div class="small">Used</div>
      <div>${bar(pct, klass)}</div>
      <div class="small mono">${p.used} / ${p.limit}</div>
    </div>
    <div class="small" style="margin-top:6px">
      Headroom <strong>${p.headroom}</strong> · resets in ${resetIn}
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
  const runs = state.runs || [];
  if (!runs.length) return '<div class="empty">— none yet</div>';
  return runs.map(r => {
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
          <button onclick="openAgent('${escape(r.session_id)}')">Open agent</button>
        </div>` : ''}
      </div>
    `;
  }).join('');
}

let lastError = null;
async function refresh() {
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const state = await r.json();
    if (state.error) throw new Error(state.error);
    $('#repo').textContent = state.repo + '  →  ' + state.base_branch;
    const flags = [];
    if (state.stop_after !== 'merge') flags.push('stop_after=' + state.stop_after);
    if (state.auto_review) flags.push('auto_review');
    if (state.dry_run) flags.push('dry_run');
    $('#meta').textContent = flags.length ? flags.join(' · ') : '';
    $('#now').innerHTML = renderNow(state);
    $('#pro').innerHTML = renderPro(state);
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
