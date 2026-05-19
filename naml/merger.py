"""Tiered merger — Phase 4 / V2 MVP marker.

After a sprint reaches ``awaiting_signoff``, the user presses [Merge
Sprint] (or runs ``naml merge``). ``merge_sprint`` walks each
``review_passed`` slice in topological order and tries to merge its PR.

Four tiers, escalating only on failure:

1. **Tier 1 — pure git/gh.** Fetch base, rebase onto ``origin/<base>``,
   re-run gates, ``gh pr merge --squash --delete-branch``. Zero LLM cost.
   Expected hit rate: 70-80% per design doc.
2. **Tier 2 — scripted resolvers.** Mechanical conflicts (lockfile,
   gitignore-style) get fixed by :mod:`naml.resolvers`. After every
   resolver applies, gates run and a normal ``gh pr merge`` finishes
   the slice.
3. **Tier 3 — fresh merger agent.** A oneshot Claude session with the
   curated sprint context (slice spec, summaries, reviewer verdict,
   gate output, conflict list) resolves whatever Tier 2 couldn't.
   Single attempt — no infinite loops.
4. **Tier 4 — human escalation.** Sprint state flips to
   ``merge_blocked``; the slice gets a PR comment with diagnostics and
   sits in ``merge_blocked`` until a human takes over.

Each attempt is appended to ``state/merge-log.json`` (see
:func:`naml.state.append_merge_log`). The on-disk sprint state advances:
``awaiting_signoff → merging → complete | merge_blocked |
partial_failure``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import claude as claude_mod
from . import gitops
from . import project_state as project_state_mod
from . import prompts
from . import resolvers
from . import state as state_mod
from . import states
from .config import NamlConfig
from .gates import GateResult, run_gates
from .package import Sprint


log = logging.getLogger("naml.merger")


# Wall-clock cap for any single Tier-3 merger agent run.
_TIER3_CAP_MINUTES_DEFAULT = 20


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class MergeOutcome:
    """In-memory mirror of one merge attempt."""

    slice_id: str
    pr_url: str
    branch: str
    tier: int                  # 1-4, or 0 if we never tried (already merged / skipped)
    success: bool
    detail: str
    started_at: str
    finished_at: str
    duration_seconds: float


@dataclass
class MergeReport:
    sprint_id: str
    sprint_state: str
    outcomes: list[MergeOutcome] = field(default_factory=list)

    def merged_count(self) -> int:
        return sum(1 for o in self.outcomes if o.success)

    def blocked_count(self) -> int:
        return sum(1 for o in self.outcomes if not o.success and o.tier > 0)


# --- helpers -------------------------------------------------------------


def _save_outcome(sprint_root: Path, outcome: MergeOutcome) -> None:
    entry = state_mod.MergeLogEntry(
        slice_id=outcome.slice_id,
        pr_url=outcome.pr_url,
        branch=outcome.branch,
        tier=outcome.tier,
        success=outcome.success,
        detail=outcome.detail,
        started_at=outcome.started_at,
        finished_at=outcome.finished_at,
        duration_seconds=outcome.duration_seconds,
    )
    state_mod.append_merge_log(sprint_root, entry)


def _slice_transition(
    sprint_root: Path,
    status: state_mod.SliceStatus,
    new_state: str,
    *,
    detail: str = "",
) -> None:
    status.state = new_state
    status.record_transition(state=new_state, detail=detail)
    log.info("[%s] → %s%s", status.slice_id, new_state, f" ({detail})" if detail else "")
    state_mod.save_slice_status(sprint_root, status)


def _post_blocked_comment(
    cfg: NamlConfig,
    *,
    cwd: Path,
    branch: str,
    slice_id: str,
    tier_history: list[tuple[int, str]],
) -> None:
    """Comment on the PR explaining why naml stopped merging."""
    if not branch:
        return
    summary_lines = [
        f"## naml — merge_blocked on `{slice_id}`",
        "",
        "Naml's tiered merge pipeline could not auto-merge this PR.",
        "",
        "### Tier history",
        "",
    ]
    for tier, detail in tier_history:
        summary_lines.append(f"- **Tier {tier}**: {detail}")
    summary_lines.extend(
        [
            "",
            "The sprint is now in `merge_blocked` until a human resolves it.",
            "After resolving (typically by rebasing + merging this PR manually),",
            "run `naml merge <sprint-dir>` again to continue with any remaining slices.",
        ]
    )
    body = "\n".join(summary_lines)
    try:
        gitops.pr_comment(branch, body, cwd=cwd, repo=cfg.repo)
    except gitops.GhError as exc:  # noqa: BLE001 — defensive on comment posting
        log.warning(
            "[%s] could not post merge_blocked comment: %s", slice_id, exc
        )


def _all_slice_summaries(sprint: Sprint, sprint_root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in sprint.slices:
        body = state_mod.read_summary(sprint_root, s.id)
        if body:
            out[s.id] = body
    return out


def _upstream_summaries(
    sprint: Sprint, sprint_root: Path, slice_id: str
) -> dict[str, str]:
    s = sprint.slice_by_id(slice_id)
    return {
        dep: state_mod.read_summary(sprint_root, dep)
        for dep in s.depends_on
        if state_mod.read_summary(sprint_root, dep)
    }


# --- per-slice merge ------------------------------------------------------


class _SliceMerger:
    """Encapsulates the four-tier flow for ONE slice."""

    def __init__(
        self,
        *,
        sprint: Sprint,
        cfg: NamlConfig,
        sprint_root: Path,
        log_dir: Path,
        status: state_mod.SliceStatus,
        slice_,
    ) -> None:
        self.sprint = sprint
        self.cfg = cfg
        self.sprint_root = sprint_root
        self.log_dir = log_dir
        self.status = status
        self.slice = slice_
        self.tier_history: list[tuple[int, str]] = []
        self.merge_log_path = (
            log_dir / sprint.id / f"{slice_.id}.merge.log"
        )

    @property
    def worktree(self) -> Path:
        return Path(self.status.worktree) if self.status.worktree else gitops.lane_worktree_path(
            lane_root=self.cfg.repo_root.parent / "naml-worktrees",
            repo_slug=self.cfg.repo,
            sprint_id=self.sprint.id,
            slice_id=self.slice.id,
        )

    @property
    def branch(self) -> str:
        return self.status.branch or f"naml/{self.sprint.id}/{self.slice.id}"

    def _ensure_worktree(self) -> Path:
        """Make sure the slice's worktree exists; recreate if missing."""
        wt = self.worktree
        if wt.is_dir():
            return wt
        # Lane cleanup or external deletion: recreate from origin/<branch>.
        gitops.fetch_base(self.cfg.repo_root, self.cfg.base_branch)
        # First, fetch the slice branch so we have an origin ref to base on.
        try:
            gitops.git(
                "fetch", "origin", self.branch, cwd=self.cfg.repo_root
            )
        except gitops.GitError:
            pass
        wt.parent.mkdir(parents=True, exist_ok=True)
        try:
            gitops.git(
                "worktree", "add",
                "--detach",
                str(wt),
                f"origin/{self.branch}",
                cwd=self.cfg.repo_root,
            )
            # Materialize a local tracking branch.
            gitops.git("checkout", "-B", self.branch, cwd=wt)
        except gitops.GitError as exc:
            raise RuntimeError(f"could not recreate worktree: {exc}") from exc
        return wt

    # ----- tier 1 ----------------------------------------------------

    def _tier1(self) -> tuple[bool, str, GateResult | None]:
        """Pure git/gh path. Returns (success, detail, last_gate_result)."""
        wt = self._ensure_worktree()
        # If a previous run interrupted mid-rebase, abort it so Tier 1
        # restarts from a clean tree. Idempotent — tolerates "no rebase
        # in progress" silently.
        gitops.rebase_abort(cwd=wt)
        # Refresh base.
        try:
            gitops.fetch_base(wt, self.cfg.base_branch)
        except gitops.GitError as exc:
            return False, f"fetch failed: {exc}", None

        try:
            gitops.rebase_onto(
                f"origin/{self.cfg.base_branch}", cwd=wt
            )
        except gitops.RebaseConflict as exc:
            return False, f"rebase paused: {exc}", None
        except gitops.GitError as exc:
            return False, f"rebase error: {exc}", None

        # Re-run gates on the rebased tree.
        gate_result = run_gates(
            self.cfg.gates,
            cwd=wt,
            log_path=self.merge_log_path,
        )
        if not gate_result.passed:
            return False, (
                f"gate '{gate_result.failed_gate}' failed after rebase"
            ), gate_result

        # Force-push the rebased branch (we own it; --force-with-lease).
        try:
            gitops.push_branch(self.branch, cwd=wt)
        except gitops.GitError as exc:
            return False, f"push after rebase failed: {exc}", gate_result

        # Squash-merge via gh.
        try:
            gitops.merge_pr_squash(
                self.branch, cwd=wt, repo=self.cfg.repo
            )
        except gitops.GhError as exc:
            return False, f"gh pr merge failed: {exc}", gate_result

        return True, "merged via Tier 1 (pure git/gh)", gate_result

    # ----- tier 2 ----------------------------------------------------

    def _tier2(self) -> tuple[bool, str, GateResult | None]:
        """Scripted resolvers on the conflicted files."""
        wt = self._ensure_worktree()
        conflicts = resolvers.list_conflict_files(worktree=wt)
        if not conflicts:
            # Tier 1 reported failure, but there are no conflict files —
            # something else (gates? push?) blocked us. Tier 2 can't help.
            return False, "no conflict files to resolve", None

        applied: list[str] = []
        for rel in conflicts:
            outcome = resolvers.try_resolve(rel, worktree=wt)
            if not outcome.applied:
                # Some other resolver hasn't claimed it — escalate.
                return False, (
                    f"no scripted resolver for `{rel}`"
                ), None
            if not outcome.resolved:
                return False, (
                    f"`{outcome.name}` couldn't resolve `{rel}`: {outcome.detail}"
                ), None
            applied.append(f"{rel} ({outcome.name})")

        # Continue the rebase.
        try:
            gitops.git("rebase", "--continue", cwd=wt)
        except gitops.GitError as exc:
            return False, f"rebase --continue failed: {exc}", None
        # Re-run gates.
        gate_result = run_gates(
            self.cfg.gates, cwd=wt, log_path=self.merge_log_path
        )
        if not gate_result.passed:
            return False, (
                f"gate '{gate_result.failed_gate}' failed after Tier-2 resolve"
            ), gate_result
        try:
            gitops.push_branch(self.branch, cwd=wt)
            gitops.merge_pr_squash(self.branch, cwd=wt, repo=self.cfg.repo)
        except (gitops.GitError, gitops.GhError) as exc:
            return False, f"finalize failed: {exc}", gate_result
        return True, (
            "merged via Tier 2 (resolved: " + "; ".join(applied) + ")"
        ), gate_result

    # ----- tier 3 ----------------------------------------------------

    def _tier3(self, *, last_gate: GateResult | None) -> tuple[bool, str]:
        """Fresh merger agent. One attempt."""
        wt = self._ensure_worktree()
        conflicts = resolvers.list_conflict_files(worktree=wt)
        upstream = _upstream_summaries(
            self.sprint, self.sprint_root, self.slice.id
        )
        siblings = _all_slice_summaries(self.sprint, self.sprint_root)
        prompt = prompts.merger_prompt(
            sprint=self.sprint,
            slice_=self.slice,
            branch=self.branch,
            base_branch=self.cfg.base_branch,
            conflict_files=conflicts,
            upstream_summaries=upstream,
            all_slice_summaries=siblings,
            review_text=self.status.last_error or self.status.review_verdict,
            gates_summary=(
                last_gate.tail
                if last_gate is not None and last_gate.tail
                else ""
            ),
        )
        result = claude_mod.run_merger(
            prompt=prompt,
            log_path=self.merge_log_path,
            cwd=wt,
            claude_bin=self.cfg.claude_bin,
            claude_config_dir=self.cfg.claude_config_dir,
            cap_minutes=_TIER3_CAP_MINUTES_DEFAULT,
        )
        if result.timed_out:
            return False, "merger agent hit the run cap"
        if not result.completed:
            return False, "merger agent exited unexpectedly"

        # Confirm conflicts are gone and rebase has finished.
        if resolvers.list_conflict_files(worktree=wt):
            return False, "merger agent finished but conflicts still present"
        # If a rebase is still in progress, abort + bail out.
        rebase_dir = wt / ".git" / "rebase-merge"
        rebase_apply = wt / ".git" / "rebase-apply"
        if rebase_dir.is_dir() or rebase_apply.is_dir():
            gitops.rebase_abort(cwd=wt)
            return False, "merger agent did not complete the rebase"

        # Re-run gates.
        gate_result = run_gates(
            self.cfg.gates, cwd=wt, log_path=self.merge_log_path
        )
        if not gate_result.passed:
            return False, (
                f"gate '{gate_result.failed_gate}' failed after Tier-3 merge"
            )

        # Finalize.
        try:
            gitops.push_branch(self.branch, cwd=wt)
            gitops.merge_pr_squash(self.branch, cwd=wt, repo=self.cfg.repo)
        except (gitops.GitError, gitops.GhError) as exc:
            return False, f"finalize after Tier-3 failed: {exc}"
        return True, "merged via Tier 3 (fresh merger agent)"

    # ----- tier 4 ----------------------------------------------------

    def _tier4(self) -> str:
        """Escalate to human. Post comment, mark slice merge_blocked."""
        # Abort any in-progress rebase so the worktree state is sane.
        if self.status.worktree:
            wt = Path(self.status.worktree)
            if wt.is_dir():
                gitops.rebase_abort(cwd=wt)
        _post_blocked_comment(
            self.cfg,
            cwd=self.cfg.repo_root,
            branch=self.branch,
            slice_id=self.slice.id,
            tier_history=self.tier_history,
        )
        return "escalated to human (PR comment posted)"

    # ----- run -------------------------------------------------------

    def run(self, *, tier_cap: int = 4) -> MergeOutcome:
        """Run the slice through the tier cascade. tier_cap restricts how
        far we escalate (e.g. tier_cap=1 = "Tier 1 only — no resolvers, no
        merger agent, no human comment")."""
        started_at = _now_iso()
        t0 = time.time()

        # Idempotency: if the PR already merged, mark + bail without spending.
        existing_state = "UNKNOWN"
        try:
            existing_state = gitops.pr_state(
                self.branch, cwd=self.cfg.repo_root, repo=self.cfg.repo
            )
        except gitops.GhError:
            pass
        if existing_state == "MERGED":
            _slice_transition(
                self.sprint_root, self.status, states.MERGED,
                detail="already merged on remote",
            )
            return MergeOutcome(
                slice_id=self.slice.id,
                pr_url=self.status.pr_url,
                branch=self.branch,
                tier=0,
                success=True,
                detail="already merged on remote — skipped",
                started_at=started_at,
                finished_at=_now_iso(),
                duration_seconds=time.time() - t0,
            )

        _slice_transition(
            self.sprint_root, self.status, states.MERGING,
            detail="entering merge pipeline",
        )

        last_gate: GateResult | None = None

        # Tier 1
        ok, detail, last_gate = self._tier1()
        self.tier_history.append((1, detail))
        if ok:
            _slice_transition(
                self.sprint_root, self.status, states.MERGED, detail=detail
            )
            return self._outcome(tier=1, success=True, detail=detail,
                                 started_at=started_at, t0=t0)

        if tier_cap < 2:
            return self._fail_with_blocked(detail, started_at, t0, last_tier=1)

        # Tier 2
        ok, detail, last_gate = self._tier2()
        self.tier_history.append((2, detail))
        if ok:
            _slice_transition(
                self.sprint_root, self.status, states.MERGED, detail=detail
            )
            return self._outcome(tier=2, success=True, detail=detail,
                                 started_at=started_at, t0=t0)

        if tier_cap < 3:
            return self._fail_with_blocked(detail, started_at, t0, last_tier=2)

        # Tier 3
        ok, detail = self._tier3(last_gate=last_gate)
        self.tier_history.append((3, detail))
        if ok:
            _slice_transition(
                self.sprint_root, self.status, states.MERGED, detail=detail
            )
            return self._outcome(tier=3, success=True, detail=detail,
                                 started_at=started_at, t0=t0)

        if tier_cap < 4:
            return self._fail_with_blocked(detail, started_at, t0, last_tier=3)

        # Tier 4
        detail = self._tier4()
        self.tier_history.append((4, detail))
        _slice_transition(
            self.sprint_root, self.status, states.MERGE_BLOCKED, detail=detail
        )
        return self._outcome(tier=4, success=False, detail=detail,
                             started_at=started_at, t0=t0)

    def _outcome(
        self, *, tier: int, success: bool, detail: str,
        started_at: str, t0: float,
    ) -> MergeOutcome:
        return MergeOutcome(
            slice_id=self.slice.id,
            pr_url=self.status.pr_url,
            branch=self.branch,
            tier=tier,
            success=success,
            detail=detail,
            started_at=started_at,
            finished_at=_now_iso(),
            duration_seconds=time.time() - t0,
        )

    def _fail_with_blocked(
        self, detail: str, started_at: str, t0: float, *, last_tier: int,
    ) -> MergeOutcome:
        """Cap-induced stop: mark merge_blocked at the cap-reached tier.

        Post a PR comment with the tier history — same human-escalation
        artefact as a full Tier-4 path. A `merge_blocked` slice is always
        accompanied by a comment so the human has something to land on.
        """
        # Abort any in-progress rebase so the worktree state is sane.
        if self.status.worktree:
            wt = Path(self.status.worktree)
            if wt.is_dir():
                gitops.rebase_abort(cwd=wt)
        _post_blocked_comment(
            self.cfg,
            cwd=self.cfg.repo_root,
            branch=self.branch,
            slice_id=self.slice.id,
            tier_history=self.tier_history,
        )
        _slice_transition(
            self.sprint_root, self.status, states.MERGE_BLOCKED,
            detail=f"tier_cap={last_tier} reached: {detail}",
        )
        return MergeOutcome(
            slice_id=self.slice.id,
            pr_url=self.status.pr_url,
            branch=self.branch,
            tier=last_tier,
            success=False,
            detail=detail,
            started_at=started_at,
            finished_at=_now_iso(),
            duration_seconds=time.time() - t0,
        )


# --- top-level entry -----------------------------------------------------


_DEFAULT_LOG_ROOT = Path.home() / "Library" / "Logs" / "naml"


def _resolve_log_root(cfg: NamlConfig) -> Path:
    if cfg.log_dir:
        return cfg.log_dir
    return _DEFAULT_LOG_ROOT / cfg.repo.replace("/", "-")


def merge_sprint(
    sprint: Sprint,
    cfg: NamlConfig,
    *,
    tier_cap: int = 4,
) -> MergeReport:
    """Merge every review-clean slice. Process in topological order so
    dependencies merge before dependents.

    The first ``merge_blocked`` slice halts the sprint at the sprint-level
    (``merge_blocked``). Downstream slices that depend on the blocked one
    won't be tried; independent slices later in the topological order WILL
    be tried so a single conflict doesn't strand the rest of the sprint.
    """
    sprint_root = cfg.sprints_path / sprint.id
    sprint_state = state_mod.load_sprint_state(sprint_root) or state_mod.SprintState(
        sprint_id=sprint.id
    )

    if sprint_state.state != states.SPRINT_MERGING:
        sprint_state.state = states.SPRINT_MERGING
        sprint_state.record_transition(
            state=states.SPRINT_MERGING,
            detail=f"tier_cap={tier_cap}",
        )
        state_mod.save_sprint_state(sprint_root, sprint_state)

    log_root = _resolve_log_root(cfg)
    log_dir = log_root / "merger"
    log_dir.mkdir(parents=True, exist_ok=True)

    report = MergeReport(sprint_id=sprint.id, sprint_state=sprint_state.state)
    blocked_ids: set[str] = set()

    for slice_id in sprint.topological_order():
        slice_ = sprint.slice_by_id(slice_id)
        status = state_mod.load_slice_status(sprint_root, slice_id)
        if status is None:
            log.warning("[%s] no status on disk — skipping", slice_id)
            continue

        # Skip slices that aren't ready to merge.
        if status.state == states.MERGED:
            continue
        if status.state in states.LANE_FAILED_STATES:
            log.info(
                "[%s] in failed-state %s — skipping merge", slice_id, status.state
            )
            blocked_ids.add(slice_id)
            continue
        # MERGING means a prior run started the merge but didn't finish
        # (most often: human interrupt of a Tier-3 agent). Resume by
        # restarting from Tier 1 — `_SliceMerger.run` will abort any
        # in-progress rebase first, so the retry begins from a clean tree.
        if status.state not in {states.REVIEW_PASSED, states.MERGING}:
            log.info(
                "[%s] state=%s — only review_passed / merging slices are merged",
                slice_id, status.state,
            )
            continue
        if status.state == states.MERGING:
            log.info(
                "[%s] resuming from interrupted merge — restarting at Tier 1",
                slice_id,
            )
        # If any declared upstream is blocked, skip this slice too.
        if any(dep in blocked_ids for dep in slice_.depends_on):
            log.info("[%s] depends on a blocked slice — skipping", slice_id)
            blocked_ids.add(slice_id)
            continue

        merger = _SliceMerger(
            sprint=sprint,
            cfg=cfg,
            sprint_root=sprint_root,
            log_dir=log_dir,
            status=status,
            slice_=slice_,
        )
        outcome = merger.run(tier_cap=tier_cap)
        _save_outcome(sprint_root, outcome)
        report.outcomes.append(outcome)
        if not outcome.success:
            blocked_ids.add(slice_id)

    # Sprint-level rollup.
    final = _final_sprint_state(sprint, sprint_root)
    if final != sprint_state.state:
        sprint_state.state = final
        sprint_state.record_transition(
            state=final,
            detail=(
                f"{report.merged_count()} merged, "
                f"{report.blocked_count()} blocked"
            ),
        )
    state_mod.save_sprint_state(sprint_root, sprint_state)
    report.sprint_state = final

    project_state_mod.on_sprint_merge_finished(
        project_state_mod.naml_dir_for(cfg),
        sprint.id,
        sprint_state=final,
        merged_count=report.merged_count(),
        blocked_count=report.blocked_count(),
    )

    return report


def _final_sprint_state(sprint: Sprint, sprint_root: Path) -> str:
    """Rollup of slice states into a sprint-level state after merge_sprint."""
    per_slice: dict[str, str] = {}
    for s in sprint.slices:
        status = state_mod.load_slice_status(sprint_root, s.id)
        per_slice[s.id] = status.state if status else "pending"

    if not per_slice:
        return states.SPRINT_FAILED

    merged = sum(1 for v in per_slice.values() if v == states.MERGED)
    blocked = sum(
        1 for v in per_slice.values()
        if v in {states.MERGE_BLOCKED, states.NEEDS_HUMAN_REVIEW, states.FAILED}
    )

    if merged == len(per_slice):
        return states.SPRINT_COMPLETE
    if blocked > 0:
        return states.SPRINT_MERGE_BLOCKED
    return states.SPRINT_PARTIAL_FAILURE
