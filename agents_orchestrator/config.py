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
    pro_limit: int
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
    pro_limit = _env_int("AO_PRO_LIMIT", int(claude_tbl.get("pro_limit", 45)))

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
        pro_limit=pro_limit,
        dry_run=dry_run,
        log_dir=log_dir,
    )
    _cache = cfg
    return cfg


_cache: Config | None = None


def reset_cache() -> None:
    """Drop the cached Config. Used by tests."""
    global _cache
    _cache = None


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
    "PRO_LIMIT": "pro_limit",
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
