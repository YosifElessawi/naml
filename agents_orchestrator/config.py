"""Central configuration for agents-orchestrator.

The orchestrator is project-agnostic. Per-project knobs live in a TOML file at
the target repo's root (``.agents-orchestrator.toml``): repo slug, base branch,
gate commands, labels. Cross-cutting knobs (run caps, Pro window) have TOML
defaults and ``AO_*`` env-var overrides so they can be tuned per-invocation
without editing the file.

Resolution is lazy on first attribute access — importing this module does not
require a config file to be present (useful for tooling like ``--help``).
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_FILENAME = ".agents-orchestrator.toml"


# --- env helpers ----------------------------------------------------------

def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be an integer, got {raw!r}")


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip() in {"1", "true", "TRUE", "yes"}


def _expand(path: str | Path) -> Path:
    return Path(os.path.expandvars(str(path))).expanduser()


# --- dataclasses ----------------------------------------------------------

@dataclass(frozen=True)
class Gate:
    name: str
    argv: list[str]


@dataclass(frozen=True)
class Labels:
    ready: str = "ready-for-agent"
    running: str = "agent-running"
    done: str = "agent-done"
    failed: str = "agent-failed"
    needs_info: str = "needs-info"


@dataclass(frozen=True)
class Config:
    # Repo
    repo: str                       # e.g. "owner/name"
    repo_root: Path                 # local working copy
    base_branch: str                # e.g. "master"

    # Gates + labels
    gates: list[Gate]
    labels: Labels

    # Run caps
    run_cap_minutes: int
    max_retries: int

    # Burst safety rails
    burst_max_issues: int
    burst_max_hours: int
    burst_min_headroom: int

    # Batching
    batch_label_prefix: str         # default "batch:"
    batch_max_size: int             # default 4

    # Pipeline
    stop_after: str                 # one of: implement, validate, pr, review, merge
    auto_review: bool               # run /review before merge when stop_after=merge

    # Claude
    claude_config_dir: Path         # for transcript reads only
    claude_bin: str
    # Plan caps — token-based. Anthropic does not publish exact numbers;
    # the user fills these in from their own /usage view. When unset
    # (None), the cockpit shows raw tokens without a percent bar.
    session_token_limit: int | None = None
    weekly_token_limit: int | None = None
    # ISO-8601 datetime of the NEXT session reset, as shown in Claude's
    # /usage view ("Resets 9:50am" → e.g. "2026-05-17T09:50:00+02:00").
    # The cockpit rolls this forward in memory by 5h once it passes — so
    # you only have to set it once per account.
    session_reset_at: str | None = None
    # Same for the weekly reset ("Resets May 23 at 4pm" → ISO datetime).
    weekly_reset_at: str | None = None
    # Minimum free session-tokens required before a burst-mode run starts.
    burst_min_tokens: int = 100_000
    # Multiplier applied to the computed (API-retail) cost so users on
    # Pro/Max subscriptions can dial their cockpit cost numbers to match
    # what Claude Code itself shows. 1.0 = no adjustment.
    cost_calibration: float = 1.0
    pro_window_seconds: int = 5 * 60 * 60

    # Behaviour
    dry_run: bool = False

    # Paths
    log_dir: Path = field(default_factory=Path)

    @property
    def runs_jsonl(self) -> Path:
        return self.log_dir / "runs.jsonl"

    @property
    def current_json(self) -> Path:
        return self.log_dir / "current.json"

    def ensure_log_dir(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)


# --- loaders --------------------------------------------------------------

def _find_config_file(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: cwd) looking for the TOML file."""
    here = (start or Path.cwd()).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    raise SystemExit(
        f"agents-orchestrator: no {CONFIG_FILENAME} found in {here} or any parent. "
        f"Place one at the root of the target repo (see templates/.agents-orchestrator.toml.example)."
    )


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _gates_from_toml(raw: list[dict[str, Any]] | None) -> list[Gate]:
    if not raw:
        raise SystemExit(
            "agents-orchestrator: at least one [[gates]] entry is required. "
            "Each gate needs `name` and `argv`."
        )
    gates: list[Gate] = []
    for i, entry in enumerate(raw):
        name = entry.get("name")
        argv = entry.get("argv")
        if not isinstance(name, str) or not name:
            raise SystemExit(f"[[gates]] #{i}: missing or empty `name`")
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise SystemExit(f"[[gates]] '{name}': `argv` must be a list of strings")
        gates.append(Gate(name=name, argv=argv))
    return gates


def _resolve_claude_config_dir(toml_value: str | None) -> Path:
    """Pick the dir from which to read transcripts.

    Order: TOML override → CLAUDE_CONFIG_DIR env → ~/.claude. Note: this is
    only used for *reading* transcripts (Pro-window estimate). When spawning a
    `claude` subprocess we pass the current env through unmodified — the user
    switches accounts by exporting CLAUDE_CONFIG_DIR in their shell.
    """
    if toml_value:
        return _expand(toml_value)
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env:
        return _expand(env)
    return Path.home() / ".claude"


def _default_log_dir(repo: str) -> Path:
    slug = repo.replace("/", "-") or "default"
    return Path.home() / "Library" / "Logs" / "agents-orchestrator" / slug


def load(start: Path | None = None) -> Config:
    """Load and validate the per-project config. Cached after first call."""
    global _cache
    if _cache is not None:
        return _cache

    path = _find_config_file(start)
    raw = _load_toml(path)

    repo_tbl = raw.get("repo") or {}
    repo = repo_tbl.get("slug")
    if not isinstance(repo, str) or "/" not in repo:
        raise SystemExit(
            f"{path}: [repo] slug must be set to 'owner/name' (got {repo!r})"
        )
    base_branch = repo_tbl.get("base_branch", "master")
    repo_root_raw = repo_tbl.get("root")
    if repo_root_raw:
        repo_root = _expand(repo_root_raw)
    else:
        # Default: the directory the config file lives in.
        repo_root = path.parent
    repo_root = Path(_env_str("AO_REPO_ROOT", str(repo_root))).expanduser()

    labels_tbl = raw.get("labels") or {}
    labels = Labels(
        ready=labels_tbl.get("ready", Labels.ready),
        running=labels_tbl.get("running", Labels.running),
        done=labels_tbl.get("done", Labels.done),
        failed=labels_tbl.get("failed", Labels.failed),
        needs_info=labels_tbl.get("needs_info", Labels.needs_info),
    )

    gates = _gates_from_toml(raw.get("gates"))

    run_tbl = raw.get("run") or {}
    run_cap_minutes = _env_int("AO_RUN_CAP_MINUTES", int(run_tbl.get("cap_minutes", 30)))
    max_retries = _env_int("AO_MAX_RETRIES", int(run_tbl.get("max_retries", 2)))

    burst_tbl = raw.get("burst") or {}
    burst_max_issues = _env_int("AO_BURST_MAX_ISSUES", int(burst_tbl.get("max_issues", 5)))
    burst_max_hours = _env_int("AO_BURST_MAX_HOURS", int(burst_tbl.get("max_hours", 4)))
    burst_min_headroom = _env_int("AO_BURST_MIN_HEADROOM", int(burst_tbl.get("min_headroom", 8)))

    batch_tbl = raw.get("batch") or {}
    batch_label_prefix = _env_str("AO_BATCH_PREFIX", batch_tbl.get("label_prefix", "batch:"))
    batch_max_size = _env_int("AO_BATCH_MAX_SIZE", int(batch_tbl.get("max_size", 4)))

    pipeline_tbl = raw.get("pipeline") or {}
    stop_after_default = pipeline_tbl.get("stop_after", "merge")
    stop_after = _env_str("AO_STOP_AFTER", stop_after_default).lower()
    # The pipeline stops AFTER the named stage completes:
    #   pr     — PR opened, not merged (the old dry-run behaviour).
    #   review — PR opened + auto-review posted, not merged.
    #   merge  — full pipeline (default).
    valid_stages = {"pr", "review", "merge"}
    if stop_after not in valid_stages:
        raise SystemExit(
            f"stop_after must be one of {sorted(valid_stages)}, got {stop_after!r}"
        )
    auto_review_default = bool(pipeline_tbl.get("auto_review", False))
    auto_review = _env_bool("AO_AUTO_REVIEW") or auto_review_default

    claude_tbl = raw.get("claude") or {}
    claude_config_dir = _resolve_claude_config_dir(claude_tbl.get("config_dir"))
    claude_bin = _env_str("AO_CLAUDE_BIN", claude_tbl.get("bin", "claude"))

    def _opt_int_limit(toml_key: str, env_key: str) -> int | None:
        env_v = os.environ.get(env_key, "").strip()
        if env_v:
            try:
                v = int(env_v.replace("_", ""))
                return v if v > 0 else None
            except ValueError:
                raise SystemExit(f"{env_key} must be a positive integer, got {env_v!r}")
        v = claude_tbl.get(toml_key)
        if v is None:
            return None
        try:
            iv = int(v)
            return iv if iv > 0 else None
        except (TypeError, ValueError):
            raise SystemExit(f"[claude] {toml_key} must be a positive integer, got {v!r}")

    session_token_limit = _opt_int_limit("session_token_limit", "AO_SESSION_TOKEN_LIMIT")
    weekly_token_limit = _opt_int_limit("weekly_token_limit", "AO_WEEKLY_TOKEN_LIMIT")

    def _opt_str(toml_key: str, env_key: str) -> str | None:
        env_v = os.environ.get(env_key, "").strip()
        if env_v:
            return env_v
        v = claude_tbl.get(toml_key)
        if v is None:
            return None
        return str(v).strip() or None

    session_reset_at = _opt_str("session_reset_at", "AO_SESSION_RESET_AT")
    weekly_reset_at = _opt_str("weekly_reset_at", "AO_WEEKLY_RESET_AT")
    burst_min_tokens = _env_int(
        "AO_BURST_MIN_TOKENS",
        int(burst_tbl.get("min_tokens", 100_000)),
    )

    cost_cal_raw = os.environ.get("AO_COST_CALIBRATION", "").strip()
    if cost_cal_raw:
        try:
            cost_calibration = float(cost_cal_raw)
        except ValueError:
            raise SystemExit(f"AO_COST_CALIBRATION must be a number, got {cost_cal_raw!r}")
    else:
        try:
            cost_calibration = float(claude_tbl.get("cost_calibration", 1.0))
        except (TypeError, ValueError):
            raise SystemExit("[claude] cost_calibration must be a number")
    if cost_calibration <= 0:
        cost_calibration = 1.0

    logs_tbl = raw.get("logs") or {}
    log_dir_raw = logs_tbl.get("dir")
    log_dir = _expand(log_dir_raw) if log_dir_raw else _default_log_dir(repo)
    log_dir = Path(_env_str("AO_LOG_DIR", str(log_dir))).expanduser()

    dry_run = _env_bool("AO_DRY_RUN")
    # dry_run is back-compat shorthand for stop_after=pr. If both are set,
    # the more conservative wins (stop earlier).
    if dry_run and stop_after == "merge":
        stop_after = "pr"

    cfg = Config(
        repo=repo,
        repo_root=repo_root,
        base_branch=base_branch,
        gates=gates,
        labels=labels,
        run_cap_minutes=run_cap_minutes,
        max_retries=max_retries,
        burst_max_issues=burst_max_issues,
        burst_max_hours=burst_max_hours,
        burst_min_headroom=burst_min_headroom,
        batch_label_prefix=batch_label_prefix,
        batch_max_size=batch_max_size,
        stop_after=stop_after,
        auto_review=auto_review,
        claude_config_dir=claude_config_dir,
        claude_bin=claude_bin,
        session_token_limit=session_token_limit,
        weekly_token_limit=weekly_token_limit,
        session_reset_at=session_reset_at,
        weekly_reset_at=weekly_reset_at,
        burst_min_tokens=burst_min_tokens,
        cost_calibration=cost_calibration,
        dry_run=dry_run,
        log_dir=log_dir,
    )
    _cache = cfg
    return cfg


_cache: Config | None = None


def reset_cache() -> None:
    """Drop the cached Config. Used by tests and after writing the TOML."""
    global _cache
    _cache = None


# --- TOML edit-in-place --------------------------------------------------
#
# Preserves comments and formatting. Only handles scalar values (string, int,
# float, bool). Lists / inline tables go through unchanged — for those, point
# the user at the file directly.

import re as _re

_SECTION_RE = _re.compile(r"^\s*\[([^\]\[]+)\]\s*$")
_KEY_RE = _re.compile(r"^(\s*)(#\s*)?([A-Za-z_][\w-]*)\s*=\s*(.*?)\s*$")


def _fmt_toml(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        # Underscore-separate thousands for readability on big numbers.
        if abs(v) >= 1000:
            s = str(abs(v))
            chunks = []
            while len(s) > 3:
                chunks.append(s[-3:])
                s = s[:-3]
            chunks.append(s)
            joined = "_".join(reversed(chunks))
            return ("-" + joined) if v < 0 else joined
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise ValueError(f"unsupported TOML scalar type: {type(v).__name__}")


def edit_toml(path: Path, edits: dict[tuple[str | None, str], Any]) -> None:
    """Edit scalar fields in a TOML file. Preserves comments and formatting.

    edits: mapping of ``(section, key)`` → new value. ``section=None`` means
    top-level. Value ``None`` comments the line out (no-op if the key wasn't
    in the file). For keys not present in their section, the line is
    injected at the END OF THE EXISTING SECTION — never appended as a new
    duplicate section header (which would be invalid TOML).
    """
    if not edits:
        return
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Parse into ordered chunks: each chunk is (section_name|None, [lines]).
    # The first chunk (section_name=None) holds any top-level lines before
    # the first [header].
    chunks: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in lines:
        m = _SECTION_RE.match(line)
        if m:
            chunks.append((m.group(1).strip(), [line]))
        else:
            chunks[-1][1].append(line)

    pending = dict(edits)

    # Pass 1: in-place replace / comment-out for keys that already exist.
    new_chunks: list[tuple[str | None, list[str]]] = []
    for sec_name, sec_lines in chunks:
        new_lines: list[str] = []
        for line in sec_lines:
            m_key = _KEY_RE.match(line)
            if m_key:
                indent, comment, key, _val_rest = m_key.groups()
                target = (sec_name, key)
                if target in pending:
                    new_v = pending.pop(target)
                    if new_v is None:
                        if not comment:
                            new_lines.append(f"{indent}# {key} = {_val_rest}\n")
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(f"{indent}{key} = {_fmt_toml(new_v)}\n")
                    continue
            new_lines.append(line)
        new_chunks.append((sec_name, new_lines))

    # Pass 2: any remaining edits with a non-None value need to be injected
    # at the END of their section (or appended as a fresh section if the
    # section doesn't exist).
    remaining: dict[str | None, list[tuple[str, Any]]] = {}
    for (sec, key), v in pending.items():
        if v is None:
            continue
        remaining.setdefault(sec, []).append((key, v))

    if remaining:
        # Map section_name → chunk index for in-place insertion.
        sec_index: dict[str | None, int] = {}
        for i, (sec, _) in enumerate(new_chunks):
            sec_index.setdefault(sec, i)

        # Inject into existing sections.
        for sec, kvs in list(remaining.items()):
            if sec in sec_index:
                idx = sec_index[sec]
                _sec, body = new_chunks[idx]
                # Insert just before the trailing blank lines of the section
                # so the next section header still gets visual separation.
                cut = len(body)
                while cut > 0 and body[cut - 1].strip() == "":
                    cut -= 1
                inserts = [f"{k} = {_fmt_toml(v)}\n" for k, v in kvs]
                new_chunks[idx] = (_sec, body[:cut] + inserts + body[cut:])
                remaining.pop(sec)

        # Append new sections that didn't exist.
        for sec, kvs in remaining.items():
            tail: list[str] = []
            # Ensure separation from prior content.
            if new_chunks and new_chunks[-1][1] and not new_chunks[-1][1][-1].endswith("\n"):
                tail.append("\n")
            tail.append("\n")
            if sec is not None:
                tail.append(f"[{sec}]\n")
            for k, v in kvs:
                tail.append(f"{k} = {_fmt_toml(v)}\n")
            new_chunks.append((sec, tail))

    out: list[str] = []
    for _, body in new_chunks:
        out.extend(body)

    # Atomic write.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(out), encoding="utf-8")
    tmp.replace(path)


def config_path() -> Path:
    """Path to the active .agents-orchestrator.toml."""
    return _find_config_file()


# --- back-compat module-level accessors -----------------------------------
# Older code (and the rest of this package) reads e.g. config.REPO_ROOT.
# Resolve those through __getattr__ so the TOML is only loaded when actually
# needed — keeps `--help` and import-time tooling free of config requirements.

_PROXY_ATTRS = {
    "REPO": "repo",
    "REPO_ROOT": "repo_root",
    "BASE_BRANCH": "base_branch",
    "GATES": "gates",
    "RUN_CAP_MINUTES": "run_cap_minutes",
    "MAX_RETRIES": "max_retries",
    "BURST_MAX_ISSUES": "burst_max_issues",
    "BURST_MAX_HOURS": "burst_max_hours",
    "BURST_MIN_HEADROOM": "burst_min_headroom",
    "BATCH_LABEL_PREFIX": "batch_label_prefix",
    "BATCH_MAX_SIZE": "batch_max_size",
    "STOP_AFTER": "stop_after",
    "AUTO_REVIEW": "auto_review",
    "CLAUDE_CONFIG_DIR": "claude_config_dir",
    "CLAUDE_BIN": "claude_bin",
    "SESSION_TOKEN_LIMIT": "session_token_limit",
    "WEEKLY_TOKEN_LIMIT": "weekly_token_limit",
    "BURST_MIN_TOKENS": "burst_min_tokens",
    "PRO_WINDOW_SECONDS": "pro_window_seconds",
    "DRY_RUN": "dry_run",
    "LOG_DIR": "log_dir",
    "RUNS_JSONL": "runs_jsonl",
    "CURRENT_JSON": "current_json",
}


def _label(field: str) -> str:
    return getattr(load().labels, field)


_LABEL_ATTRS = {
    "LABEL_READY": "ready",
    "LABEL_RUNNING": "running",
    "LABEL_DONE": "done",
    "LABEL_FAILED": "failed",
    "LABEL_NEEDS_INFO": "needs_info",
}


def __getattr__(name: str) -> Any:
    if name in _PROXY_ATTRS:
        return getattr(load(), _PROXY_ATTRS[name])
    if name in _LABEL_ATTRS:
        return _label(_LABEL_ATTRS[name])
    raise AttributeError(f"module 'config' has no attribute {name!r}")


def ensure_log_dir() -> None:
    load().ensure_log_dir()
