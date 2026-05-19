"""``naml`` command-line entry point.

Surface:

- ``naml migrate-config [--dry-run] [--root PATH]``
- ``naml show-config [--root PATH]``
- ``naml inspect-sprint <sprint-dir>``
- ``naml status``              — print the project + current sprint + slice
                                  hierarchy (same payload as /api/state)
- ``naml serve [--bind H] [--port P]`` — HTTP server exposing /api/state
                                  for the dashboard
- ``naml run <sprint-dir>``    — execute a sprint package
- ``naml merge <sprint-dir>``  — walk review-clean slices through the
                                  4-tier merge pipeline (Phase 4 / MVP)
"""

from __future__ import annotations

import argparse
import json
import logging
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
from .scheduler import ScheduleError


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


def _cmd_status(args: argparse.Namespace) -> int:
    from . import project_state as project_state_mod

    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except ConfigError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1

    payload = project_state_mod.build_hierarchy(cfg)
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from . import web as web_mod

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except ConfigError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1

    web_mod.serve(cfg, host=args.bind, port=args.port)
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

    p_status = sub.add_parser(
        "status",
        help="print project + current sprint + slice hierarchy as JSON",
    )
    p_status.add_argument("--root", help="config root (default: cwd or walked up)")
    p_status.set_defaults(func=_cmd_status)

    p_serve = sub.add_parser(
        "serve",
        help="run an HTTP server exposing /api/state for the dashboard",
    )
    p_serve.add_argument("--bind", default="127.0.0.1", help="host to bind (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=7777, help="port (default: 7777)")
    p_serve.add_argument("--root", help="config root (default: cwd or walked up)")
    p_serve.set_defaults(func=_cmd_serve)

    p_insp = sub.add_parser(
        "inspect-sprint",
        help="parse a sprint package and print its summary + DAG metadata as JSON",
    )
    p_insp.add_argument("sprint_dir", help="path to a .naml/sprints/<id>/ directory")
    p_insp.set_defaults(func=_cmd_inspect_sprint)

    p_run = sub.add_parser(
        "run",
        help="execute a sprint package end-to-end (stops at awaiting_signoff "
             "after auto-review)",
    )
    p_run.add_argument("sprint_dir", help="path to a .naml/sprints/<id>/ directory")
    p_run.add_argument(
        "--lanes",
        type=int,
        default=None,
        help="override effective lane count (default: min(config.lanes.default, dag_width, hard_cap))",
    )
    p_run.add_argument(
        "--overlap-policy",
        choices=["strict", "abort", "warn"],
        default="strict",
        help="how to handle independent slices with overlapping touches "
             "(default: strict — force serial)",
    )
    p_run.add_argument(
        "--stop-after",
        choices=["pr", "review"],
        default="review",
        help="pipeline stage to stop after. 'pr' = PR opened, awaits human "
             "review. 'review' (default) = auto-review run, sprint pauses "
             "at awaiting_signoff. 'merge' is Phase 4.",
    )
    p_run.add_argument(
        "--root",
        help="config root (default: cwd or walked up)",
    )
    p_run.add_argument(
        "--verbose", "-v", action="store_true", help="emit lane progress to stderr",
    )
    p_run.set_defaults(func=_cmd_run)

    p_merge = sub.add_parser(
        "merge",
        help="walk review_passed slices through the 4-tier merge pipeline",
    )
    p_merge.add_argument("sprint_dir", help="path to a .naml/sprints/<id>/ directory")
    p_merge.add_argument(
        "--tier-cap",
        type=int,
        choices=[1, 2, 3, 4],
        default=4,
        help="stop after this tier even if it fails (1=git-only, 2=+resolvers, "
             "3=+merger agent, 4=+human escalation). Default: 4.",
    )
    p_merge.add_argument(
        "--root",
        help="config root (default: cwd or walked up)",
    )
    p_merge.add_argument(
        "--verbose", "-v", action="store_true",
        help="emit merge progress to stderr",
    )
    p_merge.set_defaults(func=_cmd_merge)

    return parser


def _cmd_merge(args: argparse.Namespace) -> int:
    from . import merger as merger_mod

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except ConfigError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1

    try:
        sprint = load_sprint(args.sprint_dir)
    except SprintError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1

    report = merger_mod.merge_sprint(sprint, cfg, tier_cap=args.tier_cap)

    payload = {
        "sprint_id": report.sprint_id,
        "sprint_state": report.sprint_state,
        "merged_count": report.merged_count(),
        "blocked_count": report.blocked_count(),
        "outcomes": [
            {
                "slice_id": o.slice_id,
                "branch": o.branch,
                "pr_url": o.pr_url,
                "tier": o.tier,
                "success": o.success,
                "detail": o.detail,
                "duration_seconds": round(o.duration_seconds, 2),
            }
            for o in report.outcomes
        ],
    }
    print(json.dumps(payload, indent=2))
    # Exit 0 if every attempted slice merged, 1 otherwise.
    return 0 if report.blocked_count() == 0 else 1


def _cmd_run(args: argparse.Namespace) -> int:
    # Local import — only when actually running, so test suites and other
    # subcommands don't pay for thread/runtime imports.
    from . import run as run_mod

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except ConfigError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1

    try:
        sprint = load_sprint(args.sprint_dir)
    except SprintError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1

    try:
        report = run_mod.run_sprint(
            sprint,
            cfg,
            overlap_policy=args.overlap_policy,
            lane_count=args.lanes,
            stop_after=args.stop_after,
        )
    except ScheduleError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1

    payload = {
        "sprint_id": report.sprint_id,
        "aggregate_state": report.aggregate_state,
        "lanes_effective": report.lanes_effective,
        "per_slice_state": report.per_slice_state,
        "overlap_findings": [
            {"earlier": a, "later": b, "overlap": g, "action": action}
            for a, b, g, action in report.overlap_findings
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0 if report.aggregate_state not in {"failed"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
