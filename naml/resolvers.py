"""Scripted conflict resolvers — Tier 2 of the merge pipeline.

Each resolver targets a specific file shape where conflicts are mechanical
rather than semantic. The resolver:

1. Decides whether it applies (by filename or content shape).
2. Resolves the conflict in place (writes the file with conflict markers
   removed, optionally re-runs an external command like ``pnpm install``).
3. Stages the resolved file via ``git add``.

Resolvers return a ``ResolverOutcome`` so the merger can decide whether
to escalate to Tier 3. ``applied=False`` means "this resolver isn't
relevant here, try the next"; ``applied=True, resolved=False`` means
"I tried but failed — don't try Tier 3 for this file, it's a hard
conflict".

This module is intentionally minimal — adding more resolvers should be
adding more functions registered in ``RESOLVERS``, not deepening the
existing logic. The list below covers the two highest-leverage mechanical
cases (lockfiles + gitignore); adding `.env` example files, package.json
field merges, etc. is fair game for follow-ups.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


# --- conflict-marker scanning -------------------------------------------

_MARKER_START = re.compile(r"^<{7} ")
_MARKER_MID = re.compile(r"^={7}\s*$")
_MARKER_END = re.compile(r"^>{7} ")


def has_conflict_markers(path: Path) -> bool:
    """True iff ``path`` contains git conflict markers."""
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return ("<<<<<<<" in text) and (">>>>>>>" in text)


def split_conflict_blocks(text: str) -> list[tuple[str, str, str]]:
    """Parse conflict blocks into ``(ours, theirs, base_label)`` triples.

    Returns an empty list if no well-formed conflict blocks are found.
    """
    out: list[tuple[str, str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not _MARKER_START.match(lines[i]):
            i += 1
            continue
        # Collect the "ours" block until ======.
        ours: list[str] = []
        i += 1
        while i < len(lines) and not _MARKER_MID.match(lines[i]):
            ours.append(lines[i])
            i += 1
        if i >= len(lines):
            break
        i += 1  # skip the ===== line
        # Collect the "theirs" block until >>>>>>>.
        theirs: list[str] = []
        end_label = ""
        while i < len(lines) and not _MARKER_END.match(lines[i]):
            theirs.append(lines[i])
            i += 1
        if i < len(lines):
            end_label = lines[i][8:]  # strip ">>>>>>> "
            i += 1
        out.append(("\n".join(ours), "\n".join(theirs), end_label))
    return out


# --- resolver outcome ----------------------------------------------------

@dataclass(frozen=True)
class ResolverOutcome:
    name: str
    applied: bool          # True iff this resolver took ownership of the file
    resolved: bool         # True iff the file no longer has conflicts after this resolver
    detail: str

    @classmethod
    def not_applicable(cls, name: str) -> "ResolverOutcome":
        return cls(name=name, applied=False, resolved=False, detail="")


# --- helpers -------------------------------------------------------------

def _git_add(*, cwd: Path, paths: Iterable[str]) -> None:
    subprocess.run(  # noqa: S603
        ["git", "add", "--", *paths],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


def _run(cmd: list[str], *, cwd: Path, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


# --- resolver: lockfile regen --------------------------------------------

LOCKFILES = {
    "pnpm-lock.yaml": ["pnpm", "install", "--lockfile-only"],
    "package-lock.json": ["npm", "install", "--package-lock-only", "--ignore-scripts"],
    "yarn.lock": ["yarn", "install", "--mode", "update-lockfile"],
}


def resolve_lockfile(rel_path: str, *, worktree: Path) -> ResolverOutcome:
    """Regenerate a JS/TS lockfile after a conflict.

    Strategy: take "ours" by deleting the file, then re-run the package
    manager's lockfile-only install to regenerate against the merged
    ``package.json``. Conflicts in lockfiles are almost always mechanical
    consequence of two slices both adding dependencies — the regenerated
    file resolves them by construction.

    Requires the corresponding CLI on PATH (``pnpm`` / ``npm`` / ``yarn``).
    Returns ``applied=True, resolved=False`` if the CLI is missing so the
    merger doesn't escalate blindly.
    """
    name = Path(rel_path).name
    if name not in LOCKFILES:
        return ResolverOutcome.not_applicable("lockfile-regen")

    file = worktree / rel_path
    cmd = LOCKFILES[name]

    # Delete the conflicted file before regen.
    try:
        file.unlink()
    except OSError as exc:
        return ResolverOutcome(
            name="lockfile-regen",
            applied=True,
            resolved=False,
            detail=f"could not delete {rel_path}: {exc}",
        )

    if shutil.which(cmd[0]) is None:
        return ResolverOutcome(
            name="lockfile-regen",
            applied=True,
            resolved=False,
            detail=f"{cmd[0]} not on PATH",
        )

    proc = _run(cmd, cwd=worktree, timeout=600)
    if proc.returncode != 0:
        return ResolverOutcome(
            name="lockfile-regen",
            applied=True,
            resolved=False,
            detail=f"{cmd[0]} exited {proc.returncode}: {proc.stderr.strip()[:200]}",
        )

    if not file.is_file():
        return ResolverOutcome(
            name="lockfile-regen",
            applied=True,
            resolved=False,
            detail=f"{rel_path} not regenerated",
        )

    try:
        _git_add(cwd=worktree, paths=[rel_path])
    except subprocess.CalledProcessError as exc:
        return ResolverOutcome(
            name="lockfile-regen",
            applied=True,
            resolved=False,
            detail=f"git add failed: {exc.stderr.decode(errors='replace')[:200]}",
        )

    return ResolverOutcome(
        name="lockfile-regen",
        applied=True,
        resolved=True,
        detail=f"regenerated {rel_path} via {cmd[0]}",
    )


# --- resolver: gitignore union ------------------------------------------

def resolve_gitignore_union(rel_path: str, *, worktree: Path) -> ResolverOutcome:
    """Union-merge a ``.gitignore`` (or `.dockerignore`) conflict.

    Strategy: keep every non-duplicate line from both sides of every
    conflict block in the file, preserving the order ours-then-theirs.
    Comments and blank lines pass through. ``.gitignore`` semantics are
    "any matching rule excludes" — taking the union is conservative.
    """
    name = Path(rel_path).name
    if name not in {".gitignore", ".dockerignore", ".prettierignore", ".eslintignore"}:
        return ResolverOutcome.not_applicable("gitignore-union")

    file = worktree / rel_path
    if not file.is_file():
        return ResolverOutcome.not_applicable("gitignore-union")
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ResolverOutcome(
            name="gitignore-union",
            applied=True,
            resolved=False,
            detail=f"read failed: {exc}",
        )

    if "<<<<<<<" not in text:
        # No conflict markers; nothing to do.
        return ResolverOutcome.not_applicable("gitignore-union")

    # Parse into a sequence of non-conflict lines + conflict blocks; we
    # replace each block with the union of its sides.
    new_lines: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not _MARKER_START.match(lines[i]):
            new_lines.append(lines[i])
            i += 1
            continue
        # Conflict block. Collect ours and theirs.
        i += 1
        ours: list[str] = []
        while i < len(lines) and not _MARKER_MID.match(lines[i]):
            ours.append(lines[i])
            i += 1
        if i < len(lines):
            i += 1  # skip =====
        theirs: list[str] = []
        while i < len(lines) and not _MARKER_END.match(lines[i]):
            theirs.append(lines[i])
            i += 1
        if i < len(lines):
            i += 1  # skip >>>>>>>

        # Union, preserving first-seen order.
        seen: set[str] = set()
        for line in ours + theirs:
            key = line.rstrip()
            if key in seen:
                continue
            seen.add(key)
            new_lines.append(line)

    try:
        file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except OSError as exc:
        return ResolverOutcome(
            name="gitignore-union",
            applied=True,
            resolved=False,
            detail=f"write failed: {exc}",
        )

    try:
        _git_add(cwd=worktree, paths=[rel_path])
    except subprocess.CalledProcessError as exc:
        return ResolverOutcome(
            name="gitignore-union",
            applied=True,
            resolved=False,
            detail=f"git add failed: {exc.stderr.decode(errors='replace')[:200]}",
        )

    return ResolverOutcome(
        name="gitignore-union",
        applied=True,
        resolved=True,
        detail=f"unioned {rel_path}",
    )


# --- registry + dispatcher ----------------------------------------------

Resolver = Callable[[str, "..."], ResolverOutcome]  # signature: (rel_path, *, worktree)


RESOLVERS: tuple[Callable[..., ResolverOutcome], ...] = (
    resolve_lockfile,
    resolve_gitignore_union,
)


def try_resolve(rel_path: str, *, worktree: Path) -> ResolverOutcome:
    """Try each registered resolver in order. Returns the first ``applied``
    outcome (regardless of resolved/not). Returns ``not_applicable`` if no
    resolver claims the file."""
    for resolver in RESOLVERS:
        out = resolver(rel_path, worktree=worktree)
        if out.applied:
            return out
    return ResolverOutcome.not_applicable("none")


def list_conflict_files(*, worktree: Path) -> list[str]:
    """Return repo-relative paths of files git reports as conflicted in
    the current rebase / merge state."""
    proc = _run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=worktree,
        timeout=30,
    )
    if proc.returncode != 0:
        return []
    return [
        line.strip() for line in proc.stdout.splitlines() if line.strip()
    ]
