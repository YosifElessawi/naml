"""Thin wrappers around ``gh`` and ``git`` for the orchestrator.

All GitHub mutations go through here so the inner loop reads as plain English.
Every call fails loudly: a non-zero exit raises ``GhError``/``GitError`` with
the captured stderr, never a silent swallow.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from . import config


class GhError(RuntimeError):
    """A ``gh`` CLI invocation exited non-zero."""


class GitError(RuntimeError):
    """A ``git`` invocation exited non-zero."""


def _run(cmd: list[str], *, cwd: str | None = None) -> str:
    cfg = config.load()
    result = subprocess.run(
        cmd,
        cwd=cwd or str(cfg.repo_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tool = cmd[0]
        err = GhError if tool == "gh" else GitError
        raise err(f"{' '.join(cmd)} exited {result.returncode}\n{result.stderr.strip()}")
    return result.stdout


# --- git ------------------------------------------------------------------

def git(*args: str) -> str:
    """Run a git command inside the repo and return stdout."""
    return _run(["git", *args])


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").strip()


def fetch_base() -> None:
    """Refresh the base branch ref from origin."""
    git("fetch", "origin", config.load().base_branch)


def create_branch(name: str) -> None:
    """Create ``name`` from origin/<base> and check it out.

    Any in-progress work on the current branch is left untouched — the
    orchestrator owns the working copy while it runs.
    """
    fetch_base()
    git("checkout", "-B", name, f"origin/{config.load().base_branch}")


def push_branch(name: str) -> None:
    """Push an agent-owned branch. Uses --force-with-lease because the
    branch is cut fresh from origin/<base> on each run; a prior failed
    run may have left a remote tip that diverged from the new local
    history. The branch is named `agent/issue-N-…` and not collaborated
    on, so overwriting it is safe — --force-with-lease still refuses if
    someone else has pushed unrelated commits to it concurrently."""
    git("push", "-u", "--force-with-lease", "origin", name)


def diff_has_changes(branch: str) -> bool:
    """True if ``branch`` has commits that differ from origin/<base>."""
    base = config.load().base_branch
    out = git("diff", "--stat", f"origin/{base}...{branch}")
    return bool(out.strip())


def has_merge_conflict(branch: str) -> bool:
    """True if merging ``branch`` into origin/<base> would conflict.

    Uses ``git merge-tree --write-tree`` (git >= 2.38), which performs the
    merge in memory without touching the working tree. A non-zero exit means
    conflict.
    """
    cfg = config.load()
    result = subprocess.run(
        ["git", "merge-tree", "--write-tree", f"origin/{cfg.base_branch}", branch],
        cwd=str(cfg.repo_root),
        capture_output=True,
        text=True,
    )
    # Exit 0 = clean, 1 = conflict, >1 = couldn't run (treat as conflict-unknown
    # → conservative: report conflict so the human inspects).
    return result.returncode != 0


# --- gh: issues -----------------------------------------------------------

def _gh_json(args: list[str]) -> Any:
    out = _run(["gh", *args, "--repo", config.load().repo])
    return json.loads(out) if out.strip() else None


def list_ready_issues() -> list[dict]:
    """Open issues labelled ready, oldest first."""
    cfg = config.load()
    issues = _gh_json([
        "issue", "list",
        "--label", cfg.labels.ready,
        "--state", "open",
        "--json", "number,title,createdAt,labels",
        "--limit", "100",
    ]) or []
    issues.sort(key=lambda i: i["createdAt"])
    return issues


def batch_id_of(issue: dict) -> str | None:
    """Return the batch id of an issue, or None if it has no batch label.

    A batch label looks like ``<prefix><id>`` — e.g. ``batch:auth-flow``.
    """
    prefix = config.load().batch_label_prefix
    for lbl in issue.get("labels") or []:
        name = lbl.get("name") if isinstance(lbl, dict) else lbl
        if isinstance(name, str) and name.startswith(prefix):
            return name[len(prefix):] or None
    return None


def group_into_batches(issues: list[dict]) -> list[tuple[str | None, list[dict]]]:
    """Group ready issues by batch label, capped at config.batch_max_size.

    Returns a list of (batch_id, [issues]) tuples. batch_id is None for
    ungrouped (size-1) issues. Order: by oldest issue createdAt across
    batches; within a batch, by issue number ascending.
    """
    cfg = config.load()
    groups: dict[str | None, list[dict]] = {}
    seen_solo = 0
    for issue in issues:
        bid = batch_id_of(issue)
        if bid is None:
            # Each ungrouped issue is its own batch; use a unique sentinel
            # so they don't collide.
            key = f"__solo_{seen_solo}"
            seen_solo += 1
            groups[key] = [issue]
        else:
            groups.setdefault(bid, []).append(issue)

    result: list[tuple[str | None, list[dict]]] = []
    for key, members in groups.items():
        members.sort(key=lambda i: i["number"])
        # Honor the size cap — extra issues fall through to the next pass.
        capped = members[: cfg.batch_max_size]
        public_id = None if key.startswith("__solo_") else key
        result.append((public_id, capped))

    result.sort(key=lambda b: b[1][0]["createdAt"])
    return result


def get_issue(number: int) -> dict:
    return _gh_json([
        "issue", "view", str(number),
        "--json", "number,title,body,labels,url",
    ])


def add_label(number: int, label: str) -> None:
    cfg = config.load()
    _run(["gh", "issue", "edit", str(number), "--add-label", label, "--repo", cfg.repo])


def remove_label(number: int, label: str) -> None:
    cfg = config.load()
    # Removing a label that isn't present makes gh exit non-zero; tolerate it.
    result = subprocess.run(
        ["gh", "issue", "edit", str(number), "--remove-label", label, "--repo", cfg.repo],
        cwd=str(cfg.repo_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "not found" not in result.stderr.lower():
        raise GhError(f"remove-label {label} on #{number} failed\n{result.stderr.strip()}")


def set_agent_label(number: int, label: str) -> None:
    """Move an issue to exactly one agent-state label.

    Also strips the queue label: once a run starts (or finishes), the issue has
    left the queue, so leaving the queue label on would make the orchestrator
    pick the same issue again on the next pass.
    """
    lbls = config.load().labels
    for state in (lbls.ready, lbls.running, lbls.done, lbls.failed):
        if state != label:
            remove_label(number, state)
    add_label(number, label)


def comment(number: int, body: str) -> None:
    _run(["gh", "issue", "comment", str(number), "--body", body, "--repo", config.load().repo])


# --- gh: pull requests ----------------------------------------------------

def create_pr(branch: str, title: str, body: str, *, draft: bool = False) -> str:
    """Open a PR for ``branch`` against the base branch. Returns its URL."""
    cfg = config.load()
    cmd = [
        "gh", "pr", "create",
        "--repo", cfg.repo,
        "--base", cfg.base_branch,
        "--head", branch,
        "--title", title,
        "--body", body,
    ]
    if draft:
        cmd.append("--draft")
    out = _run(cmd)
    return out.strip().splitlines()[-1] if out.strip() else ""


def branch_exists_on_remote(branch: str) -> bool:
    out = git("ls-remote", "--heads", "origin", branch)
    return bool(out.strip())


def merge_pr(branch: str) -> None:
    """Squash-merge the PR for ``branch`` and delete the branch.

    Note: when stacked PRs are introduced (Phase B/C), the caller must check
    for open dependent PRs before invoking this — deleting a base branch
    auto-closes any PR that targets it. Retarget dependents or merge
    bottom-up. See roadmap.md for the rule.
    """
    cfg = config.load()
    _run([
        "gh", "pr", "merge", branch,
        "--repo", cfg.repo,
        "--squash", "--delete-branch",
    ])
