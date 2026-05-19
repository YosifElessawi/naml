"""``naml`` command-line entry point.

Phase 1 surface (intentionally tiny):

- ``naml migrate-config [--dry-run] [--root PATH]``
- ``naml show-config [--root PATH]``
- ``naml inspect-sprint <sprint-dir>``  — read-only debug helper

Later phases reintroduce the orchestrator commands (run, merge, status,
serve) under this same ``naml`` entry point against the v2 sprint package
format. Phase 2 (issue #4) adds ``naml run`` / ``naml merge``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import (
    LEGACY_CONFIG_FILENAME,
    NEW_CONFIG_RELPATH,
    ConfigError,
    load_config,
)
from .package import SprintError, load_sprint


def _cmd_migrate_config(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve() if args.root else Path.cwd().resolve()
    legacy = root / LEGACY_CONFIG_FILENAME
    new_path = root / NEW_CONFIG_RELPATH

    if not legacy.is_file():
        print(f"naml: no {LEGACY_CONFIG_FILENAME} at {root}", file=sys.stderr)
        return 1
    if new_path.is_file() and not args.force:
        print(
            f"naml: {new_path} already exists. Pass --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(f"would copy {legacy} → {new_path}")
        return 0

    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(legacy, new_path)
    print(f"wrote {new_path}")
    print(
        f"(left {legacy} in place; remove it once you've confirmed naml v2 reads "
        f"the new file correctly)"
    )
    return 0


def _cmd_inspect_sprint(args: argparse.Namespace) -> int:
    try:
        sprint = load_sprint(args.sprint_dir)
    except SprintError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1
    summary = {
        "id": sprint.id,
        "title": sprint.title,
        "target_repo": sprint.target_repo,
        "base_branch": sprint.base_branch,
        "kind": sprint.kind,
        "parent_sprint": sprint.parent_sprint,
        "slices": [
            {
                "id": s.id,
                "title": s.title,
                "type": s.type,
                "depends_on": list(s.depends_on),
                "touches": list(s.touches),
                "adrs": [str(p) for p in s.adrs],
            }
            for s in sprint.slices
        ],
        "topological_order": sprint.topological_order(),
        "dag_width": sprint.dag_width(),
        "overlap_warnings": [
            {"a": a, "b": b, "overlap": g} for a, b, g in sprint.overlap_warnings()
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_show_config(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except ConfigError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1
    payload = {
        "source_path": str(cfg.source_path),
        "source_format": cfg.source_format,
        "repo": cfg.repo,
        "repo_root": str(cfg.repo_root),
        "base_branch": cfg.base_branch,
        "gates": [{"name": g.name, "argv": g.argv} for g in cfg.gates],
        "sprints_dir": str(cfg.sprints_dir),
        "sprints_path": str(cfg.sprints_path),
        "feedback_inbox": str(cfg.feedback_inbox),
        "parallel_lanes_default": cfg.parallel_lanes_default,
        "parallel_lanes_max": cfg.parallel_lanes_max,
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="naml",
        description="naml — sprint-driven orchestrator for Claude Code agents",
    )
    parser.add_argument("--version", action="version", version=f"naml {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mig = sub.add_parser(
        "migrate-config",
        help="copy .agents-orchestrator.toml to .naml/config.toml (strict rename)",
    )
    p_mig.add_argument("--root", help="repo root (default: cwd)")
    p_mig.add_argument(
        "--dry-run", action="store_true", help="print what would happen, don't write"
    )
    p_mig.add_argument(
        "--force", action="store_true", help="overwrite an existing .naml/config.toml"
    )
    p_mig.set_defaults(func=_cmd_migrate_config)

    p_show = sub.add_parser(
        "show-config", help="print the resolved project config as JSON"
    )
    p_show.add_argument("--root", help="start directory for config discovery")
    p_show.set_defaults(func=_cmd_show_config)

    p_insp = sub.add_parser(
        "inspect-sprint",
        help="parse a sprint package and print its summary + DAG metadata as JSON",
    )
    p_insp.add_argument("sprint_dir", help="path to a .naml/sprints/<id>/ directory")
    p_insp.set_defaults(func=_cmd_inspect_sprint)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
