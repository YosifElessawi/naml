"""``naml`` command-line entry point.

Surface:

- ``naml migrate-config [--dry-run] [--root PATH]``
- ``naml show-config [--root PATH]``
- ``naml inspect-sprint <sprint-dir>``
- ``naml status``              — print the project + current sprint + slice
                                  hierarchy (same payload as /api/state)
- ``naml serve [--bind H] [--port P]`` — aiohttp server exposing /healthz,
                                  /api/state, /state, and the built web/
                                  bundle for the cockpit
- ``naml run <sprint-dir>``    — execute a sprint package (exits 2 if any
                                  configured gate's executable is missing)
- ``naml retry <sprint-dir> <slice-id>`` — reset a failed slice's status
                                  so the next ``naml run`` reprocesses it
- ``naml recover <sprint-dir> <slice-id>`` — read-only: print actionable
                                  info about a slice's prior run (branch,
                                  worktree, PR, commits since base) so the
                                  user can manually inspect or salvage work
- ``naml merge <sprint-dir>``  — walk review-clean slices through the
                                  4-tier merge pipeline (Phase 4 / MVP)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
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
    from . import server as server_mod

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except ConfigError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 1

    server_mod.serve(cfg, host=args.bind, port=args.port)
    return 0


def _cmd_retry(args: argparse.Namespace) -> int:
    from . import state as state_mod
    from . import states as states_mod

    sprint_root = Path(args.sprint_dir).expanduser().resolve()
    if not sprint_root.is_dir():
        print(
            f"naml: sprint directory not found: {sprint_root}",
            file=sys.stderr,
        )
        return 1

    status = state_mod.load_slice_status(sprint_root, args.slice_id)
    if status is None:
        print(
            f"naml: no status file for slice {args.slice_id!r} under {sprint_root}",
            file=sys.stderr,
        )
        return 1

    if status.state not in states_mod.LANE_FAILED_STATES:
        print(
            f"naml: slice {args.slice_id} is in state {status.state!r}; "
            f"only failed/blocked_upstream/merge_blocked slices can be retried",
            file=sys.stderr,
        )
        return 1

    status.state = states_mod.PENDING
    status.attempts = {}
    status.last_error = ""
    # Clear the session_id too. Leaving it behind would let Bug 2 bite on
    # the next ``naml run``: Claude rejects spawn with "Session ID is
    # already in use" when a consumed UUID is re-used. The lane regenerates
    # session_id on every claim anyway, but we wipe it here so the
    # persisted file accurately reflects "this slice has not been claimed
    # by any session yet".
    status.session_id = ""
    status.record_transition(
        state=states_mod.PENDING,
        detail="retry requested via naml retry",
    )
    state_mod.save_slice_status(sprint_root, status)

    print(
        f"retried {args.slice_id}: state=pending, "
        f"attempts + session_id cleared"
    )
    return 0


def _cmd_recover(args: argparse.Namespace) -> int:
    from . import state as state_mod

    sprint_root = Path(args.sprint_dir).expanduser().resolve()
    if not sprint_root.is_dir():
        print(
            f"naml: sprint directory not found: {sprint_root}",
            file=sys.stderr,
        )
        return 1

    status = state_mod.load_slice_status(sprint_root, args.slice_id)
    if status is None:
        print(
            f"naml: no status file for slice {args.slice_id!r} under {sprint_root}",
            file=sys.stderr,
        )
        return 1

    # Try to discover the sprint's configured base_branch from the manifest;
    # fall back to "main" if the manifest isn't parseable from here. Recover
    # is best-effort — never abort on a stale/missing manifest.
    base_branch = "main"
    try:
        from .package import load_sprint as _load_sprint  # local import
        sprint = _load_sprint(sprint_root)
        if sprint.base_branch:
            base_branch = sprint.base_branch
    except Exception:  # noqa: BLE001 — recover is best-effort
        pass

    # Header.
    print(f"{status.slice_id} (state={status.state})")

    def _line(label: str, value: str) -> None:
        print(f"  {label:<12} {value}")

    _line("worktree:", status.worktree or "(none recorded)")
    _line("branch:", status.branch or "(none recorded)")
    if status.pr_url:
        _line("pr_url:", status.pr_url)
    if status.last_error:
        _line("last_error:", status.last_error)
    if status.attempts:
        _line("attempts:", json.dumps(status.attempts, sort_keys=True))
    if status.session_id:
        _line("session_id:", status.session_id)

    # Commits on the slice branch, listed from the worktree's git context.
    print()
    wt = Path(status.worktree) if status.worktree else None
    branch = status.branch
    if not wt or not wt.is_dir():
        print("  Commits on this branch:")
        print("    (worktree directory no longer exists)")
    elif not branch:
        print("  Commits on this branch:")
        print("    (no branch recorded for this slice)")
    else:
        print(f"  Commits on this branch (since {base_branch}):")
        try:
            result = subprocess.run(  # noqa: S603 — argv list, not shell
                [
                    "git", "log", "--oneline",
                    f"{base_branch}..{branch}", "--",
                ],
                cwd=wt,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"    (git log failed: {exc})")
        else:
            if result.returncode != 0:
                stderr = (result.stderr or "").strip().splitlines()
                detail = stderr[-1] if stderr else f"exit {result.returncode}"
                print(f"    (git log failed: {detail})")
            else:
                lines = result.stdout.splitlines()
                if not lines:
                    print("    (no commits on this branch)")
                else:
                    for line in lines:
                        print(f"    {line}")

    # Summary file pointer (the implementer agent writes one per slice).
    summary_p = state_mod.summary_path(sprint_root, status.slice_id)
    if summary_p.is_file():
        try:
            n_lines = sum(1 for _ in summary_p.open("r", encoding="utf-8"))
        except OSError:
            n_lines = 0
        print()
        print("  Summary written by the agent:")
        # Show the path RELATIVE to sprint_root.parent.parent (i.e. the
        # ``.naml/sprints/<id>/state/...`` form) when possible so it's
        # copy-pasteable into ``cat``.
        try:
            rel = summary_p.relative_to(sprint_root.parent.parent)
        except ValueError:
            rel = summary_p
        print(f"    {rel} ({n_lines} lines)")

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
        help="run the cockpit HTTP server (healthz, api/state, state, web/dist)",
    )
    p_serve.add_argument("--bind", default="127.0.0.1", help="host to bind (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8765, help="port (default: 8765)")
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

    p_retry = sub.add_parser(
        "retry",
        help="reset a failed slice's status so the next naml run reprocesses it",
    )
    p_retry.add_argument(
        "sprint_dir",
        help="path to a .naml/sprints/<id>/ directory",
    )
    p_retry.add_argument("slice_id", help="slice id (e.g. slice-1)")
    p_retry.add_argument(
        "--root",
        help="config root (default: cwd or walked up); accepted for "
             "convention; this subcommand does not read config directly",
    )
    p_retry.set_defaults(func=_cmd_retry)

    p_recover = sub.add_parser(
        "recover",
        help="read-only: print actionable info about a slice's prior run",
    )
    p_recover.add_argument(
        "sprint_dir",
        help="path to a .naml/sprints/<id>/ directory",
    )
    p_recover.add_argument("slice_id", help="slice id (e.g. slice-1)")
    p_recover.add_argument(
        "--root",
        help="config root (default: cwd or walked up); accepted for "
             "convention; this subcommand does not read config directly",
    )
    p_recover.set_defaults(func=_cmd_recover)

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
    except run_mod.GatePreflightError as exc:
        print(f"naml: {exc}", file=sys.stderr)
        return 2
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
    if report.aggregate_state == "paused":
        print(
            f"\nnaml: sprint {report.sprint_id} paused. "
            f"Re-run `naml run {args.sprint_dir}` to resume from where this left off.",
            file=sys.stderr,
        )
        return 0
    return 0 if report.aggregate_state not in {"failed"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
