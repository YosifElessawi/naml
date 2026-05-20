"""External-merge reconciler.

Detects slices whose PRs were merged outside of naml's tiered merger
(e.g. the user clicked "Squash and merge" on GitHub during a rescue, or
a stacked-PR base was merged elsewhere) and walks them through to
``merged`` so the cockpit + sprint rollup reflect reality.

Without this, slices stay at ``review_passed`` indefinitely and the
cockpit shows e.g. ``2/14`` when the truth is ``14/14``. naml's normal
state machine only transitions to ``merged`` from inside :mod:`naml.merger`,
which assumes naml itself is performing the merge.

Used by:

- ``naml reconcile <sprint-dir>`` — one-shot CLI.
- ``naml run`` (start of run) — keeps sprint state honest after a rescue.
- ``naml merge`` (start of pipeline) — same.

The reconciler is idempotent: a slice already at ``merged`` is skipped,
and a PR not yet merged on GitHub stays untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import gitops, states
from . import state as state_mod
from .config import NamlConfig
from .package import Sprint

log = logging.getLogger(__name__)


# Slice states from which an external-merge transition is sane. We do
# NOT reconcile pending/setup/work — a branch in those states may not
# even have commits to merge, and treating it as merged would corrupt
# the state machine.
RECONCILABLE_STATES: frozenset[str] = frozenset({
    states.PR,
    states.REVIEW,
    states.REVIEW_PASSED,
    states.MERGING,
    states.FAILED,
    states.NEEDS_HUMAN_REVIEW,
    states.BLOCKED_UPSTREAM,
    states.MERGE_BLOCKED,
})


@dataclass
class ReconcileOutcome:
    slice_id: str
    previous_state: str
    new_state: str
    detail: str
    changed: bool


@dataclass
class ReconcileReport:
    sprint_id: str
    outcomes: list[ReconcileOutcome] = field(default_factory=list)

    @property
    def changed_count(self) -> int:
        return sum(1 for o in self.outcomes if o.changed)


def reconcile_sprint(
    sprint: Sprint,
    cfg: NamlConfig,
    sprint_root: Path,
    *,
    pr_state_fn=gitops.pr_state,
) -> ReconcileReport:
    """Scan ``sprint``'s slices and promote externally-merged ones.

    ``pr_state_fn`` is injectable for tests; defaults to the real ``gh``-
    backed lookup. Failures from gh (network blip, auth) are logged and
    the slice is left untouched — we'd rather be honest than guess.
    """
    report = ReconcileReport(sprint_id=sprint.id)
    for slice_ in sprint.slices:
        status = state_mod.load_slice_status(sprint_root, slice_.id)
        if status is None:
            continue
        if status.state == states.MERGED:
            continue
        if status.state not in RECONCILABLE_STATES:
            continue
        branch = status.branch or f"naml/{sprint.id}/{slice_.id}"
        try:
            pr_state = pr_state_fn(branch, cwd=cfg.repo_root, repo=cfg.repo)
        except gitops.GhError as exc:
            log.warning("[%s] reconcile: gh failed: %s", slice_.id, exc)
            continue
        if pr_state != "MERGED":
            continue
        previous = status.state
        detail = f"reconciled — PR merged externally (was {previous})"
        status.state = states.MERGED
        status.record_transition(state=states.MERGED, detail=detail)
        state_mod.save_slice_status(sprint_root, status)
        report.outcomes.append(
            ReconcileOutcome(
                slice_id=slice_.id,
                previous_state=previous,
                new_state=states.MERGED,
                detail=detail,
                changed=True,
            )
        )
        log.info("[%s] → merged (reconciled, was %s)", slice_.id, previous)

    # Always refresh the sprint's slices map from the per-slice files,
    # even when nothing was promoted this run. The map can drift if any
    # transition (manual gh merge, prior reconcile, partial run) wrote
    # to a slice's status.json without updating sprint.json — which has
    # been the actual symptom showing in the cockpit (header showed
    # `2/14 merged` while every slice file already said `merged`).
    sprint_state = state_mod.load_sprint_state(sprint_root)
    if sprint_state is not None:
        before = dict(sprint_state.slices)
        for slice_ in sprint.slices:
            status = state_mod.load_slice_status(sprint_root, slice_.id)
            sprint_state.slices[slice_.id] = (
                status.state if status is not None else "pending"
            )
        all_merged = (
            len(sprint_state.slices) > 0
            and all(v == states.MERGED for v in sprint_state.slices.values())
        )
        rolled = False
        if all_merged and sprint_state.state != states.SPRINT_COMPLETE:
            sprint_state.state = states.SPRINT_COMPLETE
            sprint_state.record_transition(
                state=states.SPRINT_COMPLETE,
                detail=(
                    "reconciler: all slices on disk are merged "
                    f"({report.changed_count} promoted this run)"
                ),
            )
            rolled = True
        if rolled or before != sprint_state.slices:
            state_mod.save_sprint_state(sprint_root, sprint_state)

    return report
