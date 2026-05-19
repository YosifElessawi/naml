"""Project-level configuration for naml v2.

Reads ``.naml/config.toml`` (preferred) or the legacy ``.agents-orchestrator.toml``
and exposes a single ``NamlConfig`` dataclass.

The schema is a strict superset of the v1 file: every v1 key keeps its
meaning, and v2 adds ``[paths]`` (sprint directory location, feedback inbox
path) and ``[lanes]`` (default lane count, hard cap). The legacy filename
stays supported so existing target repos can run ``naml migrate-config``
on their own time instead of being forced to flip files in lockstep with
this commit.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


NEW_CONFIG_RELPATH = Path(".naml") / "config.toml"
LEGACY_CONFIG_FILENAME = ".agents-orchestrator.toml"
DEFAULT_SPRINTS_DIR = Path(".naml") / "sprints"
DEFAULT_FEEDBACK_INBOX = Path("docs") / "feedback" / "inbox.md"
DEFAULT_PARALLEL_LANES = 3
HARD_LANE_CAP = 8

# Default repo-root entries to symlink into each lane's worktree so the agent
# can resolve gitignored-but-needed paths (e.g. ``.venv/bin/ruff``). Override
# via ``[worktree] symlinks`` in the project config. Empty entries are
# silently skipped at worktree-setup time so the default list is safe on
# projects without a ``.venv``.
DEFAULT_WORKTREE_SYMLINKS: tuple[str, ...] = (".venv",)


class ConfigError(ValueError):
    """Raised when a config file is missing required keys or malformed."""


@dataclass(frozen=True)
class Gate:
    name: str
    argv: list[str]


@dataclass(frozen=True)
class Labels:
    """Label set. v1 names retained for back-compat; v2 names added."""

    # v1 (back-compat)
    ready: str = "ready-for-agent"
    running: str = "agent-running"
    done: str = "agent-done"
    failed: str = "agent-failed"
    needs_info: str = "needs-info"
    # v2 — published from manifest by the orchestrator
    naml_running: str = "naml:running"
    naml_review: str = "naml:review"
    naml_human_review: str = "naml:human-review"
    naml_done: str = "naml:done"


@dataclass(frozen=True)
class NamlConfig:
    """Per-project configuration. Same TOML schema regardless of source path."""

    # Where the config was loaded from. Useful for tooling + error messages.
    source_path: Path
    source_format: str  # "naml" | "legacy"

    # [repo]
    repo: str
    repo_root: Path
    base_branch: str

    # [[gates]]
    gates: list[Gate]

    # [labels]
    labels: Labels

    # [run]
    run_cap_minutes: int = 30
    max_retries: int = 2

    # [burst]
    burst_max_issues: int = 5
    burst_max_hours: int = 4
    burst_min_headroom: int = 8
    burst_min_tokens: int = 100_000

    # [batch] — legacy, kept for v1 interop. V2 replaces with DAG.
    batch_label_prefix: str = "batch:"
    batch_max_size: int = 4

    # [pipeline]
    stop_after: str = "merge"
    auto_review: bool = False

    # [claude]
    claude_bin: str = "claude"
    claude_config_dir: Path | None = None
    session_token_limit: int | None = None
    weekly_token_limit: int | None = None
    session_reset_at: str | None = None
    weekly_reset_at: str | None = None
    cost_calibration: float = 1.0

    # [lanes]  — v2 (with sensible defaults if absent)
    parallel_lanes_default: int = DEFAULT_PARALLEL_LANES
    parallel_lanes_max: int = HARD_LANE_CAP

    # [paths] — v2 (defaults relative to repo_root)
    sprints_dir: Path = field(default_factory=lambda: DEFAULT_SPRINTS_DIR)
    feedback_inbox: Path = field(default_factory=lambda: DEFAULT_FEEDBACK_INBOX)

    # [worktree] — v2. Repo-root paths to symlink into each lane's worktree
    # so the agent can resolve gitignored-but-needed paths (.venv,
    # node_modules, .env, etc.) from inside.
    worktree_symlinks: tuple[str, ...] = field(
        default_factory=lambda: DEFAULT_WORKTREE_SYMLINKS
    )

    # [logs]
    log_dir: Path | None = None

    @property
    def sprints_path(self) -> Path:
        """Absolute path to the sprint packages directory."""
        return self.repo_root / self.sprints_dir

    @property
    def feedback_inbox_path(self) -> Path:
        """Absolute path to the feedback inbox file."""
        return self.repo_root / self.feedback_inbox


# --- discovery + parsing --------------------------------------------------


def _walk_up(start: Path):
    here = start.resolve()
    yield here
    yield from here.parents


def find_config_file(start: Path | None = None) -> tuple[Path, str]:
    """Return ``(path, format)`` for the nearest config file.

    Preference order at each level of the walk:
    1. ``.naml/config.toml`` (new format)
    2. ``.agents-orchestrator.toml`` (legacy)

    Raises ``ConfigError`` if neither is found at any ancestor.
    """
    root = (start or Path.cwd()).resolve()
    for parent in _walk_up(root):
        new_path = parent / NEW_CONFIG_RELPATH
        if new_path.is_file():
            return new_path, "naml"
        legacy_path = parent / LEGACY_CONFIG_FILENAME
        if legacy_path.is_file():
            return legacy_path, "legacy"
    raise ConfigError(
        f"no naml config found in {root} or any parent. "
        f"Expected {NEW_CONFIG_RELPATH} or {LEGACY_CONFIG_FILENAME}."
    )


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: malformed TOML: {exc}") from exc


def _expect_str(d: dict[str, Any], section: str, key: str, path: Path) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v:
        raise ConfigError(f"{path}: [{section}] {key} must be a non-empty string")
    return v


def _gates_from(raw: Any, path: Path) -> list[Gate]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(
            f"{path}: at least one [[gates]] entry is required (each with name + argv)"
        )
    out: list[Gate] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: [[gates]] #{i} must be a table")
        name = entry.get("name")
        argv = entry.get("argv")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{path}: [[gates]] #{i} missing or empty `name`")
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise ConfigError(
                f"{path}: [[gates]] '{name}' argv must be a list of strings"
            )
        out.append(Gate(name=name, argv=list(argv)))
    return out


def _opt_positive_int(value: Any, label: str, path: Path) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{path}: {label} must be a positive integer, got {value!r}")
    return value


def _expand(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


def _resolve_path(value: Any, default: Path, base: Path) -> Path:
    """Expand a path; if relative, anchor to ``base``."""
    if value is None:
        candidate = default
    elif isinstance(value, str):
        candidate = _expand(value)
    else:
        raise ConfigError(f"path values must be strings, got {value!r}")
    return candidate if candidate.is_absolute() else base / candidate


def load_config(start: Path | None = None) -> NamlConfig:
    """Discover and parse the project config. Returns a frozen ``NamlConfig``.

    No env-var overrides yet. V2 will reintroduce them intentionally in a
    later phase, scoped to the keys that actually need runtime tuning.
    """
    path, fmt = find_config_file(start)
    raw = _load_toml(path)

    # [repo]
    repo_tbl = raw.get("repo") or {}
    if not isinstance(repo_tbl, dict):
        raise ConfigError(f"{path}: [repo] must be a table")
    repo = _expect_str(repo_tbl, "repo", "slug", path)
    if "/" not in repo:
        raise ConfigError(f"{path}: [repo] slug must be 'owner/name', got {repo!r}")
    base_branch = repo_tbl.get("base_branch", "main")
    if not isinstance(base_branch, str) or not base_branch:
        raise ConfigError(f"{path}: [repo] base_branch must be a non-empty string")

    repo_root_raw = repo_tbl.get("root")
    if repo_root_raw:
        repo_root = _expand(repo_root_raw)
    else:
        # Default: directory containing the config file. For .naml/config.toml,
        # that's the .naml/ dir — walk up one more so repo_root is the repo root.
        repo_root = path.parent.parent if fmt == "naml" else path.parent

    # [labels]
    labels_tbl = raw.get("labels") or {}
    if not isinstance(labels_tbl, dict):
        raise ConfigError(f"{path}: [labels] must be a table")
    defaults = Labels()
    labels = Labels(
        ready=str(labels_tbl.get("ready", defaults.ready)),
        running=str(labels_tbl.get("running", defaults.running)),
        done=str(labels_tbl.get("done", defaults.done)),
        failed=str(labels_tbl.get("failed", defaults.failed)),
        needs_info=str(labels_tbl.get("needs_info", defaults.needs_info)),
        naml_running=str(labels_tbl.get("naml_running", defaults.naml_running)),
        naml_review=str(labels_tbl.get("naml_review", defaults.naml_review)),
        naml_human_review=str(
            labels_tbl.get("naml_human_review", defaults.naml_human_review)
        ),
        naml_done=str(labels_tbl.get("naml_done", defaults.naml_done)),
    )

    # [[gates]]
    gates = _gates_from(raw.get("gates"), path)

    # [run]
    run_tbl = raw.get("run") or {}
    run_cap_minutes = int(run_tbl.get("cap_minutes", 30))
    max_retries = int(run_tbl.get("max_retries", 2))

    # [burst]
    burst_tbl = raw.get("burst") or {}
    burst_max_issues = int(burst_tbl.get("max_issues", 5))
    burst_max_hours = int(burst_tbl.get("max_hours", 4))
    burst_min_headroom = int(burst_tbl.get("min_headroom", 8))
    burst_min_tokens = int(burst_tbl.get("min_tokens", 100_000))

    # [batch]
    batch_tbl = raw.get("batch") or {}
    batch_label_prefix = str(batch_tbl.get("label_prefix", "batch:"))
    batch_max_size = int(batch_tbl.get("max_size", 4))

    # [pipeline]
    pipeline_tbl = raw.get("pipeline") or {}
    stop_after = str(pipeline_tbl.get("stop_after", "merge")).lower()
    if stop_after not in {"pr", "review", "merge"}:
        raise ConfigError(
            f"{path}: [pipeline] stop_after must be one of pr|review|merge, "
            f"got {stop_after!r}"
        )
    auto_review = bool(pipeline_tbl.get("auto_review", False))

    # [claude]
    claude_tbl = raw.get("claude") or {}
    claude_bin = str(claude_tbl.get("bin", "claude"))
    claude_config_dir_raw = claude_tbl.get("config_dir")
    claude_config_dir = _expand(claude_config_dir_raw) if claude_config_dir_raw else None

    session_token_limit = _opt_positive_int(
        claude_tbl.get("session_token_limit"), "[claude] session_token_limit", path
    )
    weekly_token_limit = _opt_positive_int(
        claude_tbl.get("weekly_token_limit"), "[claude] weekly_token_limit", path
    )
    session_reset_at = claude_tbl.get("session_reset_at")
    weekly_reset_at = claude_tbl.get("weekly_reset_at")
    if session_reset_at is not None and not isinstance(session_reset_at, str):
        raise ConfigError(f"{path}: [claude] session_reset_at must be a string")
    if weekly_reset_at is not None and not isinstance(weekly_reset_at, str):
        raise ConfigError(f"{path}: [claude] weekly_reset_at must be a string")

    cost_cal_raw = claude_tbl.get("cost_calibration", 1.0)
    try:
        cost_calibration = float(cost_cal_raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path}: [claude] cost_calibration must be a number") from exc
    if cost_calibration <= 0:
        cost_calibration = 1.0

    # [lanes]  (v2)
    lanes_tbl = raw.get("lanes") or {}
    parallel_lanes_default = int(lanes_tbl.get("default", DEFAULT_PARALLEL_LANES))
    parallel_lanes_max = int(lanes_tbl.get("max", HARD_LANE_CAP))
    if parallel_lanes_default < 1:
        raise ConfigError(f"{path}: [lanes] default must be >= 1")
    if parallel_lanes_max < parallel_lanes_default:
        raise ConfigError(
            f"{path}: [lanes] max ({parallel_lanes_max}) must be >= "
            f"default ({parallel_lanes_default})"
        )

    # [paths]  (v2)
    paths_tbl = raw.get("paths") or {}
    sprints_dir = _resolve_path(
        paths_tbl.get("sprints_dir"), DEFAULT_SPRINTS_DIR, repo_root
    )
    feedback_inbox = _resolve_path(
        paths_tbl.get("feedback_inbox"), DEFAULT_FEEDBACK_INBOX, repo_root
    )
    # Store relative-to-repo when possible so the dataclass stays portable.
    try:
        sprints_dir_rel = sprints_dir.relative_to(repo_root)
    except ValueError:
        sprints_dir_rel = sprints_dir
    try:
        feedback_inbox_rel = feedback_inbox.relative_to(repo_root)
    except ValueError:
        feedback_inbox_rel = feedback_inbox

    # [worktree]  (v2)
    worktree_tbl = raw.get("worktree") or {}
    if not isinstance(worktree_tbl, dict):
        raise ConfigError(f"{path}: [worktree] must be a table")
    symlinks_raw = worktree_tbl.get("symlinks")
    if symlinks_raw is None:
        worktree_symlinks: tuple[str, ...] = DEFAULT_WORKTREE_SYMLINKS
    else:
        if not isinstance(symlinks_raw, list) or not all(
            isinstance(x, str) for x in symlinks_raw
        ):
            raise ConfigError(
                f"{path}: [worktree] symlinks must be a list of strings"
            )
        worktree_symlinks = tuple(s.strip() for s in symlinks_raw if s.strip())

    # [logs]
    logs_tbl = raw.get("logs") or {}
    log_dir_raw = logs_tbl.get("dir")
    log_dir = _expand(log_dir_raw) if log_dir_raw else None

    return NamlConfig(
        source_path=path,
        source_format=fmt,
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
        burst_min_tokens=burst_min_tokens,
        batch_label_prefix=batch_label_prefix,
        batch_max_size=batch_max_size,
        stop_after=stop_after,
        auto_review=auto_review,
        claude_bin=claude_bin,
        claude_config_dir=claude_config_dir,
        session_token_limit=session_token_limit,
        weekly_token_limit=weekly_token_limit,
        session_reset_at=session_reset_at,
        weekly_reset_at=weekly_reset_at,
        cost_calibration=cost_calibration,
        parallel_lanes_default=parallel_lanes_default,
        parallel_lanes_max=parallel_lanes_max,
        sprints_dir=sprints_dir_rel,
        feedback_inbox=feedback_inbox_rel,
        worktree_symlinks=worktree_symlinks,
        log_dir=log_dir,
    )
