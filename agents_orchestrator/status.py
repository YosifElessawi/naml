"""Render the orchestrator status dashboard. Read-only — writes nothing."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from . import claude_session, config, github_ops

# --- ANSI helpers ---------------------------------------------------------

_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def _bold(t: str) -> str:
    return _c(t, "1")


def _dim(t: str) -> str:
    return _c(t, "2")


def _green(t: str) -> str:
    return _c(t, "32")


def _red(t: str) -> str:
    return _c(t, "31")


def _yellow(t: str) -> str:
    return _c(t, "33")


def _cyan(t: str) -> str:
    return _c(t, "36")


# --- time formatting ------------------------------------------------------

def _ago(iso: str) -> str:
    ts = _parse(iso)
    if ts is None:
        return "unknown"
    return _humanize(datetime.now(timezone.utc).timestamp() - ts.timestamp()) + " ago"


def _parse(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _humanize(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if m else f"{h}h"


# --- state readers --------------------------------------------------------

def _read_runs(limit: int = 5) -> list[dict]:
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


# --- sections -------------------------------------------------------------

def _bar(used: int, limit: int, width: int = 12) -> str:
    if limit <= 0:
        return "[" + "?" * width + "]"
    filled = min(width, round(width * used / limit))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _section_now_running(current: dict | None) -> list[str]:
    cfg = config.load()
    lines = ["", _bold("Now running")]
    if not current:
        lines.append("  " + _dim("─ none"))
        return lines
    started = _parse(current.get("started_at", ""))
    cap = current.get("cap_minutes", cfg.run_cap_minutes)
    elapsed = 0
    if started:
        elapsed = int(datetime.now(timezone.utc).timestamp() - started.timestamp())
    remaining = max(0, cap * 60 - elapsed)
    lines.append(
        f"  ─ issue #{current.get('issue_number')}, started {_humanize(elapsed)} ago, "
        f"{_humanize(remaining)} cap remaining"
    )
    sid = current.get("session_id")
    if sid:
        lines.append("    " + _dim(f"peek:  claude --resume {sid}"))
    return lines


def _fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _usage_line(label: str, usage: claude_session.Usage, limit: int | None,
                burst_min: int) -> list[str]:
    if not usage.ok:
        return [f"  ─ {label:<10} " + _yellow("no transcripts in this window")]
    total = usage.total_tokens
    reset = (f", resets in {_humanize(usage.reset_in_seconds)}"
             if usage.reset_in_seconds is not None else "")
    if limit:
        pct = round(100 * total / limit) if limit else 0
        tone = _green if (limit - total) > burst_min else (
               _yellow if (limit - total) > 0 else _red)
        bar = _bar(total, limit)
        return [
            f"  ─ {label:<10} {bar} {pct}% used"
            f"  ({_fmt_tok(total)} / {_fmt_tok(limit)} tokens{reset})"
            f"  cost ${usage.cost_usd:.2f}"
        ]
    # No cap configured — show raw counts only.
    return [
        f"  ─ {label:<10} " + _dim(f"{_fmt_tok(total)} tokens · ${usage.cost_usd:.2f}{reset}")
    ]


def _section_usage() -> list[str]:
    cfg = config.load()
    lines = ["", _bold("Usage (Claude plan)")]
    session = claude_session.session_usage()
    weekly = claude_session.weekly_usage()
    lines.extend(_usage_line("Session", session, cfg.session_token_limit, cfg.burst_min_tokens))
    lines.extend(_usage_line("Weekly",  weekly,  cfg.weekly_token_limit,  cfg.burst_min_tokens))
    if not cfg.session_token_limit and not cfg.weekly_token_limit:
        lines.append("  " + _dim("─ set [claude] session_token_limit / weekly_token_limit in TOML"))
        lines.append("  " + _dim("    for accurate % bars (read your caps from Claude's /usage view)"))
    return lines


def _section_cost() -> list[str]:
    today = claude_session.today_usage()
    last30 = claude_session.thirty_day_usage()
    lines = ["", _bold("Cost")]
    if today.ok:
        lines.append(f"  ─ Today           ${today.cost_usd:.2f}  ·  {_fmt_tok(today.total_tokens)} tokens")
    if last30.ok:
        lines.append(f"  ─ Last 30 days    ${last30.cost_usd:.2f}  ·  {_fmt_tok(last30.total_tokens)} tokens")
    if not today.ok and not last30.ok:
        lines.append("  " + _dim("─ no local transcripts found"))
    return lines


def _section_queue() -> list[str]:
    cfg = config.load()
    lines = ["", _bold("Queue")]
    try:
        issues = github_ops.list_ready_issues()
    except github_ops.GhError as exc:
        lines.append("  ─ " + _red(f"could not read queue: {exc}"))
        return lines
    if not issues:
        lines.append("  " + _dim("─ empty"))
        return lines
    for issue in issues:
        age = _ago(issue["createdAt"]).replace(" ago", "")
        title = issue["title"][:42]
        lines.append(
            f"  ─ #{issue['number']:<4} {title:<44} "
            f"{_cyan(cfg.labels.ready)}  {age}"
        )
    return lines


_OUTCOME_GLYPH = {
    "done": ("✓", _green),
    "failed": ("✗", _red),
    "dry-run": ("⊝", _yellow),
    "needs-info": ("?", _cyan),
    "skipped": ("·", _dim),
}


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_usage(usage: dict | None) -> str:
    """Compact one-liner: '12.3k in / 4.5k out / $0.34'."""
    if not usage:
        return ""
    inp = int(usage.get("input_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0) or 0)
    cost = float(usage.get("total_cost_usd", 0.0) or 0.0)
    if inp == 0 and out == 0 and cost == 0:
        return ""
    return f"{_fmt_tokens(inp)} in / {_fmt_tokens(out)} out / ${cost:.2f}"


def _section_recent(runs: list[dict]) -> list[str]:
    lines = ["", _bold("Recent runs (last 5)")]
    if not runs:
        lines.append("  " + _dim("─ none yet"))
        return lines
    for run in runs:
        glyph, paint = _OUTCOME_GLYPH.get(run.get("outcome", ""), ("?", _dim))
        outcome = run.get("outcome", "?")
        dur = _humanize(run.get("duration_sec", 0))
        issue = run.get("issue_number", "?")
        detail = run.get("detail", "")
        lines.append(f"  #{issue:<4} {paint(glyph + ' ' + outcome):<20} {dur:>5}   {detail}")
        usage_line = _fmt_usage(run.get("usage"))
        if usage_line:
            lines.append("       " + _dim(usage_line))
        sid = run.get("session_id")
        if sid:
            lines.append("       " + _dim(f"claude --resume {sid}"))
    return lines


def render() -> str:
    cfg = config.load()
    runs = _read_runs()
    current = _read_current()

    header = [
        _bold(_cyan(f"AGENTS-ORCHESTRATOR · {cfg.repo}")),
        "",
    ]
    last_run = runs[0] if runs else None
    last_txt = _ago(last_run["finished_at"]) if last_run and last_run.get("finished_at") else "never"
    # Reflect the in-flight run if there is one, else the most recent run, else
    # the ambient config — so the header shows what is actually happening, not
    # whatever env vars the person running `status` happens to have.
    recent = current or last_run or {}
    mode = recent.get("mode", "slow")
    dry = recent.get("dry_run", cfg.dry_run)
    header.append(f"Mode: {mode}  |  Last run: {last_txt}  |  Dry-run: {'ON' if dry else 'off'}")

    out: list[str] = []
    out.extend(header)
    out.extend(_section_now_running(current))
    out.extend(_section_usage())
    out.extend(_section_cost())
    out.extend(_section_queue())
    out.extend(_section_recent(runs))
    out.append("")
    return "\n".join(out)


def print_dashboard() -> None:
    print(render())
