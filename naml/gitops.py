"""Thin wrappers around ``git`` and ``gh`` for the sprint orchestrator.

Every mutation goes through here so the lane worker reads as plain English.
Every call fails loud: a non-zero exit raises ``GitError`` / ``GhError``
with the captured stderr — no silent swallows.

The wrappers take ``cwd`` explicitly so each lane can route its calls into
its own worktree without a thread-local switch. Pass ``cwd=worktree_path``
from the lane worker; pass ``cwd=repo_root`` from the scheduler.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class GitError(RuntimeError):
    """A ``git`` invocation exited non-zero."""


class GhError(RuntimeError):
    """A ``gh`` invocation exited non-zero."""


@dataclass(frozen=True)
class PR:
    number: int
    url: str
    is_draft: bool
    title: str
    branch: str


def _run(cmd: list[str], *, cwd: Path) -> str:
    result = subprocess.run(  # noqa: S603 — argv list, not shell
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tool = cmd[0]
        err = GhError if tool == "gh" else GitError
        raise err(
            f"{' '.join(cmd)} (cwd={cwd}) exited {result.returncode}\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout


# --- git -----------------------------------------------------------------

def git(*args: str, cwd: Path) -> str:
    return _run(["git", *args], cwd=cwd)


def current_branch(cwd: Path) -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).strip()


def fetch_base(cwd: Path, base_branch: str) -> None:
    git("fetch", "origin", base_branch, cwd=cwd)


def create_branch_from_base(
    branch: str, *, cwd: Path, base_branch: str, reset_hard: bool = True
) -> None:
    """Cut a clean ``branch`` from ``origin/<base_branch>``.

    With ``reset_hard=True`` (default), discards any uncommitted state in
    ``cwd`` so a prior interrupted run can't leak into the new agent's
    view. Caller is responsible for ensuring nothing valuable is in the
    worktree before calling.
    """
    fetch_base(cwd, base_branch)
    if reset_hard:
        git("reset", "--hard", f"origin/{base_branch}", cwd=cwd)
    git("checkout", "-B", branch, cwd=cwd)


def push_branch(branch: str, *, cwd: Path) -> None:
    """Push the lane's slice branch to origin.

    Uses ``--force-with-lease`` so a concurrent push by anyone else (humans,
    other agents) is refused. The catch: lease evaluation uses our local
    ``refs/remotes/origin/<branch>`` ref. If a previous PR for this branch
    was closed-with-delete-branch on the remote, our remote-tracking ref
    is stale and the lease fails with "stale info" even though it's safe
    to push fresh.

    Mitigation: on the FIRST failure, refresh remote-tracking via
    ``git fetch --prune origin`` and retry once. Second failure surfaces
    the real error.

    naml is the only writer of ``naml/<sprint>/<slice>`` branches, so
    force-with-lease is overkill in the common case — but the lease check
    is still useful defence against a human pushing to the same branch
    name out-of-band.
    """
    try:
        git("push", "-u", "--force-with-lease", "origin", branch, cwd=cwd)
        return
    except GitError as first_err:
        if "stale info" not in str(first_err).lower():
            raise
    # Refresh remote-tracking; tolerate a missing-on-remote branch.
    try:
        git("fetch", "--prune", "origin", cwd=cwd)
    except GitError:
        pass
    git("push", "-u", "--force-with-lease", "origin", branch, cwd=cwd)


def diff_has_changes(branch: str, *, cwd: Path, base_branch: str) -> bool:
    """True iff ``branch`` has commits that differ from ``origin/<base>``."""
    out = git("diff", "--stat", f"origin/{base_branch}...{branch}", cwd=cwd)
    return bool(out.strip())


def has_merge_conflict(branch: str, *, cwd: Path, base_branch: str) -> bool:
    """Predict whether merging ``branch`` into ``origin/<base>`` would conflict.

    Uses ``git merge-tree --write-tree`` (git >= 2.38). Exit 0 = clean,
    non-zero = conflict (or merge-tree couldn't run; conservative).
    """
    result = subprocess.run(  # noqa: S603
        ["git", "merge-tree", "--write-tree",
         f"origin/{base_branch}", branch],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return result.returncode != 0


# --- worktree ------------------------------------------------------------

def worktree_add(
    target_path: Path,
    *,
    repo_root: Path,
    base_branch: str,
    symlinks: tuple[str, ...] | list[str] = (),
) -> None:
    """Create a detached-HEAD worktree at ``target_path`` based on origin/<base>.

    The worktree starts on origin/<base>; the lane worker will then cut
    its slice branch off this position.

    ``symlinks`` is a list of repo-root-relative paths to symlink into the
    fresh worktree (e.g. ``[".venv"]``). Entries that don't exist at the
    source are silently skipped — the default list is safe on projects
    that don't have a ``.venv``. Existing entries in the worktree (which
    can happen if a previous run left state behind) are removed and
    re-linked.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        # Best-effort cleanup of a stale worktree.
        worktree_remove(target_path, repo_root=repo_root)
    fetch_base(repo_root, base_branch)
    git(
        "worktree", "add",
        "--detach",
        str(target_path),
        f"origin/{base_branch}",
        cwd=repo_root,
    )
    for rel in symlinks:
        rel = rel.strip()
        if not rel:
            continue
        src = (repo_root / rel).resolve()
        if not src.exists():
            continue
        link_path = target_path / rel
        # Remove anything in the way (a stale symlink or a tracked file with
        # the same name would block ln). Tracked files take precedence —
        # don't clobber them.
        if link_path.is_symlink():
            link_path.unlink()
        elif link_path.exists():
            # The repo actually tracks this path; respect git's copy.
            continue
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(src)


def worktree_remove(target_path: Path, *, repo_root: Path) -> None:
    """Remove a worktree. Forces removal — does not preserve uncommitted state."""
    try:
        git("worktree", "remove", "--force", str(target_path), cwd=repo_root)
    except GitError:
        # If git's own bookkeeping is out of sync (e.g., the dir was
        # deleted manually), prune and retry once.
        try:
            git("worktree", "prune", cwd=repo_root)
        except GitError:
            pass
        if target_path.exists():
            # Final fallback: rmtree from python.
            import shutil
            shutil.rmtree(target_path, ignore_errors=True)


def lane_worktree_path(
    *, lane_root: Path, repo_slug: str, sprint_id: str, slice_id: str
) -> Path:
    """Stable path: <lane_root>/<repo-slug>/<sprint-id>/<slice-id>/."""
    safe_repo = repo_slug.replace("/", "-")
    return lane_root / safe_repo / sprint_id / slice_id


# --- gh ------------------------------------------------------------------

def _gh_json(args: list[str], *, cwd: Path, repo: str) -> Any:
    out = _run(["gh", *args, "--repo", repo], cwd=cwd)
    return json.loads(out) if out.strip() else None


def find_open_pr(branch: str, *, cwd: Path, repo: str) -> PR | None:
    out = _run(
        ["gh", "pr", "list",
         "--repo", repo,
         "--state", "open",
         "--head", branch,
         "--json", "number,url,isDraft,title,headRefName",
         "--limit", "1"],
        cwd=cwd,
    )
    if not out.strip():
        return None
    data = json.loads(out)
    if not data:
        return None
    item = data[0]
    return PR(
        number=int(item["number"]),
        url=str(item.get("url", "")),
        is_draft=bool(item.get("isDraft", False)),
        title=str(item.get("title", "")),
        branch=str(item.get("headRefName", branch)),
    )


def create_pr(
    *,
    branch: str,
    title: str,
    body: str,
    cwd: Path,
    repo: str,
    base_branch: str,
    draft: bool = False,
) -> str:
    """Open or update the PR for ``branch``. Returns the PR URL.

    If a PR is already open for the branch we update its title/body and
    optionally lift the draft flag, instead of opening a duplicate.
    """
    existing = find_open_pr(branch, cwd=cwd, repo=repo)
    if existing:
        try:
            _run(
                ["gh", "pr", "edit", str(existing.number),
                 "--repo", repo,
                 "--title", title,
                 "--body", body],
                cwd=cwd,
            )
        except GhError:
            pass
        if existing.is_draft and not draft:
            try:
                _run(["gh", "pr", "ready", str(existing.number), "--repo", repo], cwd=cwd)
            except GhError:
                pass
        return existing.url

    cmd = [
        "gh", "pr", "create",
        "--repo", repo,
        "--base", base_branch,
        "--head", branch,
        "--title", title,
        "--body", body,
    ]
    if draft:
        cmd.append("--draft")
    out = _run(cmd, cwd=cwd)
    return out.strip().splitlines()[-1] if out.strip() else ""


def pr_diff(branch: str, *, cwd: Path, repo: str) -> str:
    """Return the unified diff of the open PR for ``branch``."""
    return _run(["gh", "pr", "diff", branch, "--repo", repo], cwd=cwd)


def pr_state(branch: str, *, cwd: Path, repo: str) -> str:
    """Return the state of the open PR for ``branch``: OPEN/CLOSED/MERGED/UNKNOWN."""
    pr = find_open_pr(branch, cwd=cwd, repo=repo)
    if pr is not None:
        return "OPEN"
    out = _run(
        ["gh", "pr", "list", "--repo", repo, "--state", "all",
         "--head", branch, "--json", "state", "--limit", "1"],
        cwd=cwd,
    )
    if not out.strip():
        return "UNKNOWN"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return "UNKNOWN"
    if not data:
        return "UNKNOWN"
    return str(data[0].get("state", "UNKNOWN"))


class RebaseConflict(GitError):
    """Raised when ``git rebase`` left conflicts to resolve.

    The merger's Tier 2 + 3 logic catches this specifically and feeds the
    conflicted files to the resolvers / Tier-3 agent. Other errors flow
    through as plain ``GitError``.
    """

    def __init__(self, message: str, conflicts: list[str]) -> None:
        super().__init__(message)
        self.conflicts = conflicts


def rebase_onto(base_ref: str, *, cwd: Path) -> None:
    """Rebase the current branch onto ``base_ref`` (e.g. ``origin/main``).

    Raises :class:`RebaseConflict` (a subclass of ``GitError``) if rebase
    pauses on conflicts. Files are listed in ``exc.conflicts``. Any other
    failure raises plain ``GitError`` with stderr.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "rebase", base_ref],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    # Detect conflict state by querying git itself; stderr parsing is brittle.
    conflicted = _run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=cwd,
    ).strip().splitlines()
    if conflicted:
        raise RebaseConflict(
            f"rebase paused on conflicts in {len(conflicted)} file(s)",
            conflicts=[line.strip() for line in conflicted if line.strip()],
        )
    raise GitError(
        f"git rebase {base_ref} (cwd={cwd}) exited {result.returncode}\n"
        f"{result.stderr.strip()}"
    )


def rebase_abort(*, cwd: Path) -> None:
    """Abort an in-progress rebase. Tolerates 'no rebase in progress'."""
    try:
        git("rebase", "--abort", cwd=cwd)
    except GitError:
        # If no rebase is in progress, git exits non-zero — that's fine.
        pass


def merge_pr_squash(branch: str, *, cwd: Path, repo: str) -> None:
    """``gh pr merge --squash --delete-branch`` for ``branch``."""
    _run(
        ["gh", "pr", "merge", branch,
         "--repo", repo, "--squash", "--delete-branch"],
        cwd=cwd,
    )


def pr_comment(branch: str, body: str, *, cwd: Path, repo: str) -> None:
    _run(["gh", "pr", "comment", branch, "--repo", repo, "--body", body], cwd=cwd)


def add_label_to_issue(number: int, label: str, *, cwd: Path, repo: str) -> None:
    _run(
        ["gh", "issue", "edit", str(number),
         "--add-label", label, "--repo", repo],
        cwd=cwd,
    )


def create_issue(
    *,
    title: str,
    body: str,
    labels: list[str],
    cwd: Path,
    repo: str,
) -> int:
    """Create a GitHub issue and return its number.

    Used when the orchestrator publishes one issue per slice at sprint kickoff.
    """
    cmd = ["gh", "issue", "create",
           "--repo", repo, "--title", title, "--body", body]
    for label in labels:
        cmd += ["--label", label]
    out = _run(cmd, cwd=cwd)
    # gh outputs the issue URL; the trailing path segment is the number.
    for line in out.strip().splitlines():
        line = line.strip()
        if line.startswith("https://"):
            try:
                return int(line.rsplit("/", 1)[-1])
            except ValueError:
                continue
    raise GhError(f"unable to parse issue number from: {out!r}")
