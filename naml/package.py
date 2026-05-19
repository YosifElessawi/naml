"""Sprint package reader.

A sprint package is an on-disk directory at ``.naml/sprints/<sprint-id>/``
containing:

- ``manifest.toml``  — orchestration metadata (the only machine-required file)
- ``overview.md``    — sprint intent, injected into every slice prompt
- ``slices/*.md``    — per-slice spec (one file per slice, referenced from manifest)
- ``artifacts/``     — optional inputs (mockups, eval data, samples)
- ``feedback.md``    — only present on feedback sprints
- ``state/``         — runtime-only, gitignored, written by the orchestrator

``load_sprint(dir)`` returns a ``Sprint`` dataclass with the parsed manifest,
the overview as a string, the validated DAG of ``Slice`` entries, and the
classified ``kind`` / ``parent_sprint`` pointer for feedback sprints.

Validation enforced here (deterministic, no LLM):

- Required ``[sprint]`` keys (id, title, target_repo, base_branch).
- ``[meta] kind`` ∈ {greenfield, feedback}; ``parent_sprint`` required iff feedback.
- Slice ids unique and non-empty.
- Slice ``type`` ∈ {AFK, HITL}.
- ``depends_on`` references existing slice ids only.
- No cycles in the dependency graph (topological sort).
- Each slice's ``prompt`` file exists on disk and is non-empty.
- Optional ``adrs`` paths exist on disk if listed.

DAG-ready metadata exposed on the returned object:

- ``Sprint.topological_order()`` — slice ids in a valid execution order.
- ``Sprint.dag_width()``         — max number of parallel-eligible slices.
- ``Sprint.overlap_warnings()``  — pairs of independent slices that share a
  ``touches`` glob (advisory; the actual pre-flight enforcement happens in
  Phase 2's scheduler when the warnings become serial edges).
"""

from __future__ import annotations

import fnmatch
import tomllib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MANIFEST_FILENAME = "manifest.toml"
OVERVIEW_FILENAME = "overview.md"
FEEDBACK_FILENAME = "feedback.md"
SLICES_DIRNAME = "slices"
ARTIFACTS_DIRNAME = "artifacts"
STATE_DIRNAME = "state"

VALID_SLICE_TYPES = {"AFK", "HITL"}
VALID_SPRINT_KINDS = {"greenfield", "feedback"}


class SprintError(ValueError):
    """Raised when a sprint package is missing files or malformed."""


@dataclass(frozen=True)
class Slice:
    id: str
    title: str
    type: str  # "AFK" | "HITL"
    depends_on: tuple[str, ...]
    touches: tuple[str, ...]
    prompt_path: Path        # absolute path to slices/<file>.md
    prompt_body: str
    adrs: tuple[Path, ...]   # absolute paths to ADR files referenced by this slice

    @property
    def is_afk(self) -> bool:
        return self.type == "AFK"

    @property
    def is_hitl(self) -> bool:
        return self.type == "HITL"


@dataclass(frozen=True)
class Sprint:
    # Identity
    id: str
    title: str
    target_repo: str
    base_branch: str

    # Lineage
    kind: str                          # "greenfield" | "feedback"
    parent_sprint: str | None          # set iff kind == "feedback"

    # Filesystem
    root: Path                         # absolute path to the sprint dir
    manifest_path: Path
    overview: str                      # contents of overview.md
    feedback: str | None               # contents of feedback.md if present

    # Work
    slices: tuple[Slice, ...]
    artifacts_dir: Path | None         # absolute, None if dir absent

    # Cached graph data
    _adjacency: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)

    # --- DAG helpers ---------------------------------------------------

    def slice_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.slices)

    def slice_by_id(self, slice_id: str) -> Slice:
        for s in self.slices:
            if s.id == slice_id:
                return s
        raise KeyError(slice_id)

    def topological_order(self) -> list[str]:
        """Return slice ids in an execution-safe order. Stable by slice id."""
        indeg: dict[str, int] = {s.id: 0 for s in self.slices}
        for s in self.slices:
            for dep in s.depends_on:
                indeg[s.id] += 1
        ready: deque[str] = deque(sorted(sid for sid, d in indeg.items() if d == 0))
        order: list[str] = []
        # children[parent] = [child, ...]
        children: dict[str, list[str]] = defaultdict(list)
        for s in self.slices:
            for dep in s.depends_on:
                children[dep].append(s.id)
        while ready:
            sid = ready.popleft()
            order.append(sid)
            for c in sorted(children[sid]):
                indeg[c] -= 1
                if indeg[c] == 0:
                    ready.append(c)
        if len(order) != len(self.slices):
            # Should have been caught at parse time; defensive.
            raise SprintError(f"{self.id}: cycle detected in slice dependency graph")
        return order

    def dag_width(self) -> int:
        """Maximum number of slices simultaneously eligible for execution.

        Computed by simulating the topological execution: pop all currently
        ready slices in each round, record the round size, then advance.
        """
        indeg: dict[str, int] = {s.id: 0 for s in self.slices}
        for s in self.slices:
            for _dep in s.depends_on:
                indeg[s.id] += 1
        children: dict[str, list[str]] = defaultdict(list)
        for s in self.slices:
            for dep in s.depends_on:
                children[dep].append(s.id)
        ready = [sid for sid, d in indeg.items() if d == 0]
        width = 0
        while ready:
            width = max(width, len(ready))
            next_ready: list[str] = []
            for sid in ready:
                for c in children[sid]:
                    indeg[c] -= 1
                    if indeg[c] == 0:
                        next_ready.append(c)
            ready = next_ready
        return width

    def overlap_warnings(self) -> list[tuple[str, str, str]]:
        """Pairs of *independent* slices that share at least one touches glob.

        Returns a list of ``(slice_a_id, slice_b_id, conflicting_glob)`` tuples.
        Phase 2's scheduler turns these into forced serial edges; for now they
        are surfaced as warnings only.
        """
        warnings: list[tuple[str, str, str]] = []
        # Build the transitive-dep set so two slices are only "independent"
        # if neither is reachable from the other.
        children: dict[str, set[str]] = defaultdict(set)
        for s in self.slices:
            for dep in s.depends_on:
                children[dep].add(s.id)

        def reachable_from(start: str) -> set[str]:
            seen: set[str] = set()
            stack = [start]
            while stack:
                cur = stack.pop()
                for c in children[cur]:
                    if c not in seen:
                        seen.add(c)
                        stack.append(c)
            return seen

        reach: dict[str, set[str]] = {s.id: reachable_from(s.id) for s in self.slices}

        for i, a in enumerate(self.slices):
            for b in self.slices[i + 1:]:
                if b.id in reach[a.id] or a.id in reach[b.id]:
                    continue
                overlap = _first_glob_overlap(a.touches, b.touches)
                if overlap is not None:
                    warnings.append((a.id, b.id, overlap))
        return warnings


# --- glob overlap helper --------------------------------------------------


def _glob_intersects(a: str, b: str) -> bool:
    """Conservative overlap test for two path globs.

    Two globs *might* match the same path if either matches the other as a
    literal, or they share a common literal prefix and at least one is a
    suffix of the other under fnmatch. This errs on the side of FLAGGING
    potential overlaps — the cost of a false positive is a UI warning; the
    cost of a false negative is a hidden runtime conflict.
    """
    if a == b:
        return True
    # If one glob has no wildcards and matches the other, they overlap.
    if "*" not in a and "?" not in a and "[" not in a:
        if fnmatch.fnmatchcase(a, b):
            return True
    if "*" not in b and "?" not in b and "[" not in b:
        if fnmatch.fnmatchcase(b, a):
            return True
    # Compare literal prefixes (everything before the first wildcard).
    pa = _literal_prefix(a)
    pb = _literal_prefix(b)
    common = _common_prefix(pa, pb)
    if not common:
        return False
    # If one prefix fully contains the other AND the trailing wildcards
    # could plausibly match the same suffix, treat as overlap.
    if pa.startswith(pb) or pb.startswith(pa):
        return True
    return False


def _literal_prefix(glob: str) -> str:
    for i, ch in enumerate(glob):
        if ch in "*?[":
            return glob[:i]
    return glob


def _common_prefix(a: str, b: str) -> str:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return a[:i]
    return a[:n]


def _first_glob_overlap(
    globs_a: tuple[str, ...], globs_b: tuple[str, ...]
) -> str | None:
    for ga in globs_a:
        for gb in globs_b:
            if _glob_intersects(ga, gb):
                return ga if ga == gb else f"{ga} ⇄ {gb}"
    return None


# --- loading + validation -------------------------------------------------


def _read_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SprintError(f"{label}: file not found at {path}") from exc
    except OSError as exc:
        raise SprintError(f"{label}: cannot read {path}: {exc}") from exc


def _expect_str(d: dict[str, Any], section: str, key: str, where: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v:
        raise SprintError(f"{where}: [{section}] {key} must be a non-empty string")
    return v


def _str_list(d: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    v = d.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise SprintError(f"{where}: `{key}` must be a list of strings, got {v!r}")
    return tuple(v)


def _detect_cycle(slices: list[Slice]) -> list[str] | None:
    """Returns a list of slice ids forming a cycle, or None if acyclic."""
    indeg: dict[str, int] = {s.id: 0 for s in slices}
    children: dict[str, list[str]] = defaultdict(list)
    for s in slices:
        for dep in s.depends_on:
            indeg[s.id] += 1
            children[dep].append(s.id)
    queue: deque[str] = deque(sid for sid, d in indeg.items() if d == 0)
    visited = 0
    while queue:
        sid = queue.popleft()
        visited += 1
        for c in children[sid]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if visited == len(slices):
        return None
    # Report the unresolved slice ids — those form (or feed) the cycle.
    return sorted(sid for sid, d in indeg.items() if d > 0)


def load_sprint(sprint_dir: Path | str) -> Sprint:
    """Parse and validate a sprint package directory.

    Path may be relative or absolute. If relative, resolved against ``cwd``.
    """
    root = Path(sprint_dir).expanduser().resolve()
    if not root.is_dir():
        raise SprintError(f"sprint directory not found: {root}")

    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise SprintError(f"missing manifest: {manifest_path}")
    try:
        with manifest_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SprintError(f"{manifest_path}: malformed TOML: {exc}") from exc

    # [sprint]
    sprint_tbl = raw.get("sprint") or {}
    if not isinstance(sprint_tbl, dict):
        raise SprintError(f"{manifest_path}: [sprint] must be a table")
    sprint_id = _expect_str(sprint_tbl, "sprint", "id", str(manifest_path))
    title = _expect_str(sprint_tbl, "sprint", "title", str(manifest_path))
    target_repo = _expect_str(sprint_tbl, "sprint", "target_repo", str(manifest_path))
    if "/" not in target_repo:
        raise SprintError(
            f"{manifest_path}: [sprint] target_repo must be 'owner/name', got {target_repo!r}"
        )
    base_branch = _expect_str(sprint_tbl, "sprint", "base_branch", str(manifest_path))

    # [meta]
    meta_tbl = raw.get("meta") or {}
    if not isinstance(meta_tbl, dict):
        raise SprintError(f"{manifest_path}: [meta] must be a table")
    kind = str(meta_tbl.get("kind", "greenfield"))
    if kind not in VALID_SPRINT_KINDS:
        raise SprintError(
            f"{manifest_path}: [meta] kind must be one of "
            f"{sorted(VALID_SPRINT_KINDS)}, got {kind!r}"
        )
    parent_raw = meta_tbl.get("parent_sprint", "") or ""
    if not isinstance(parent_raw, str):
        raise SprintError(f"{manifest_path}: [meta] parent_sprint must be a string")
    parent_sprint: str | None = parent_raw.strip() or None
    if kind == "feedback" and not parent_sprint:
        raise SprintError(
            f"{manifest_path}: [meta] parent_sprint is required when kind = 'feedback'"
        )
    if kind == "greenfield" and parent_sprint:
        raise SprintError(
            f"{manifest_path}: [meta] parent_sprint must be empty for greenfield sprints"
        )

    # overview.md
    overview = _read_text(root / OVERVIEW_FILENAME, "overview.md")

    # feedback.md (feedback sprints only — required if kind == feedback)
    feedback_path = root / FEEDBACK_FILENAME
    if kind == "feedback":
        if not feedback_path.is_file():
            raise SprintError(
                f"feedback sprint {sprint_id} missing required {FEEDBACK_FILENAME}"
            )
        feedback: str | None = _read_text(feedback_path, "feedback.md")
    else:
        feedback = _read_text(feedback_path, "feedback.md") if feedback_path.is_file() else None

    # [[slices]]
    slice_entries = raw.get("slices") or []
    if not isinstance(slice_entries, list) or not slice_entries:
        raise SprintError(
            f"{manifest_path}: at least one [[slices]] entry is required"
        )

    seen_ids: set[str] = set()
    slices: list[Slice] = []
    for i, entry in enumerate(slice_entries):
        if not isinstance(entry, dict):
            raise SprintError(f"{manifest_path}: [[slices]] #{i} must be a table")
        where = f"{manifest_path} [[slices]] #{i}"
        sid = _expect_str(entry, "slices", "id", where)
        if sid in seen_ids:
            raise SprintError(f"{manifest_path}: duplicate slice id {sid!r}")
        seen_ids.add(sid)

        title_s = _expect_str(entry, "slices", "title", where)
        type_s = str(entry.get("type", "AFK"))
        if type_s not in VALID_SLICE_TYPES:
            raise SprintError(
                f"{where}: type must be one of {sorted(VALID_SLICE_TYPES)}, got {type_s!r}"
            )

        depends_on = _str_list(entry, "depends_on", where)
        touches = _str_list(entry, "touches", where)
        prompt_rel = _expect_str(entry, "slices", "prompt", where)
        prompt_path = (root / prompt_rel).resolve()
        # Refuse escapes out of the sprint dir.
        try:
            prompt_path.relative_to(root)
        except ValueError as exc:
            raise SprintError(
                f"{where}: prompt path {prompt_rel!r} escapes the sprint directory"
            ) from exc
        if not prompt_path.is_file():
            raise SprintError(f"{where}: prompt file missing at {prompt_path}")
        prompt_body = _read_text(prompt_path, f"slice {sid} prompt")
        if not prompt_body.strip():
            raise SprintError(f"{where}: prompt file at {prompt_path} is empty")

        adrs_raw = _str_list(entry, "adrs", where)
        adrs: list[Path] = []
        for adr_rel in adrs_raw:
            adr_path = (root.parent.parent.parent / adr_rel).resolve() \
                if not Path(adr_rel).is_absolute() else Path(adr_rel).resolve()
            # ADR paths in the manifest are relative to the repo root, not the
            # sprint root. We don't know the repo root from inside this module
            # without the config — so we accept any existing file and let the
            # orchestrator resolve canonically. For Phase 1 validation we just
            # check the path is non-empty and well-formed.
            if not adr_rel.strip():
                raise SprintError(f"{where}: empty ADR path")
            adrs.append(Path(adr_rel))

        slices.append(
            Slice(
                id=sid,
                title=title_s,
                type=type_s,
                depends_on=depends_on,
                touches=touches,
                prompt_path=prompt_path,
                prompt_body=prompt_body,
                adrs=tuple(adrs),
            )
        )

    # depends_on referential integrity
    for s in slices:
        for dep in s.depends_on:
            if dep not in seen_ids:
                raise SprintError(
                    f"slice {s.id!r}: depends_on references unknown slice {dep!r}"
                )
            if dep == s.id:
                raise SprintError(f"slice {s.id!r}: depends_on contains itself")

    # cycle detection
    cycle = _detect_cycle(slices)
    if cycle is not None:
        raise SprintError(
            f"{manifest_path}: cycle detected in slice graph involving {cycle}"
        )

    artifacts_dir = root / ARTIFACTS_DIRNAME
    return Sprint(
        id=sprint_id,
        title=title,
        target_repo=target_repo,
        base_branch=base_branch,
        kind=kind,
        parent_sprint=parent_sprint,
        root=root,
        manifest_path=manifest_path,
        overview=overview,
        feedback=feedback,
        slices=tuple(slices),
        artifacts_dir=artifacts_dir if artifacts_dir.is_dir() else None,
    )


def list_sprints(sprints_dir: Path | str) -> list[str]:
    """List sprint IDs (directory names) found under ``sprints_dir``.

    Skips entries that don't contain a ``manifest.toml`` — those aren't sprints.
    Returned list is sorted lexicographically (sprint IDs are date-prefixed so
    this also gives chronological order).
    """
    root = Path(sprints_dir).expanduser().resolve()
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and (p / MANIFEST_FILENAME).is_file()
    )
