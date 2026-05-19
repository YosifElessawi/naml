"""Tests for naml.package — sprint package reader + DAG validation."""

from __future__ import annotations

import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from naml.package import (
    Sprint,
    SprintError,
    list_sprints,
    load_sprint,
)


def _build_sprint(
    root: Path,
    *,
    sprint_id: str = "2026-05-19-test-sprint",
    kind: str = "greenfield",
    parent_sprint: str = "",
    slices: list[dict] | None = None,
    overview: str = "Test sprint overview.\n",
    include_feedback: bool = False,
) -> Path:
    """Create a sprint package on disk and return its root path."""
    sprint_dir = root / sprint_id
    (sprint_dir / "slices").mkdir(parents=True)
    (sprint_dir / "overview.md").write_text(overview, encoding="utf-8")
    if include_feedback or kind == "feedback":
        (sprint_dir / "feedback.md").write_text("- bullet one\n", encoding="utf-8")

    slices = slices or [
        {
            "id": "slice-1",
            "title": "First slice",
            "type": "AFK",
            "depends_on": [],
            "touches": ["src/a/**"],
        },
        {
            "id": "slice-2",
            "title": "Second slice",
            "type": "AFK",
            "depends_on": ["slice-1"],
            "touches": ["src/b/**"],
        },
    ]
    slice_blocks: list[str] = []
    for s in slices:
        prompt_rel = s.get("prompt") or f"slices/{s['id']}.md"
        prompt_path = sprint_dir / prompt_rel
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(f"# {s['id']}\n\nBody for {s['id']}.\n", encoding="utf-8")
        deps = ", ".join(f'"{d}"' for d in s.get("depends_on", []))
        touches = ", ".join(f'"{t}"' for t in s.get("touches", []))
        adrs = ", ".join(f'"{a}"' for a in s.get("adrs", []))
        slice_blocks.append(
            textwrap.dedent(
                f"""
                [[slices]]
                id         = "{s['id']}"
                title      = "{s['title']}"
                type       = "{s['type']}"
                depends_on = [{deps}]
                touches    = [{touches}]
                prompt     = "{prompt_rel}"
                adrs       = [{adrs}]
                """
            ).strip()
        )

    manifest = textwrap.dedent(
        f"""
        [sprint]
        id          = "{sprint_id}"
        title       = "Test sprint"
        target_repo = "owner/repo"
        base_branch = "main"

        [meta]
        parent_sprint = "{parent_sprint}"
        kind          = "{kind}"

        """
    ).lstrip() + "\n\n".join(slice_blocks) + "\n"
    (sprint_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
    return sprint_dir


class _TempSprintsMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-pkg-")).resolve()

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)


class LoadSprintHappyPathTests(_TempSprintsMixin, unittest.TestCase):
    def test_parses_basic_two_slice_sprint(self) -> None:
        path = _build_sprint(self._tmp)
        sprint = load_sprint(path)
        self.assertIsInstance(sprint, Sprint)
        self.assertEqual(sprint.id, "2026-05-19-test-sprint")
        self.assertEqual(sprint.kind, "greenfield")
        self.assertIsNone(sprint.parent_sprint)
        self.assertEqual(sprint.target_repo, "owner/repo")
        self.assertEqual(sprint.base_branch, "main")
        self.assertEqual(len(sprint.slices), 2)
        self.assertEqual(sprint.overview, "Test sprint overview.\n")
        self.assertTrue(sprint.slices[0].is_afk)

    def test_topological_order_and_dag_width(self) -> None:
        # Diamond: 1 → {2, 3} → 4
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": []},
            {"id": "slice-2", "title": "B", "type": "AFK", "depends_on": ["slice-1"], "touches": []},
            {"id": "slice-3", "title": "C", "type": "AFK", "depends_on": ["slice-1"], "touches": []},
            {"id": "slice-4", "title": "D", "type": "AFK", "depends_on": ["slice-2", "slice-3"], "touches": []},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        sprint = load_sprint(path)
        order = sprint.topological_order()
        # slice-1 first; slice-4 last.
        self.assertEqual(order[0], "slice-1")
        self.assertEqual(order[-1], "slice-4")
        # slice-2 and slice-3 must come before slice-4.
        self.assertLess(order.index("slice-2"), order.index("slice-4"))
        self.assertLess(order.index("slice-3"), order.index("slice-4"))
        # Max parallel-eligible step = the middle layer (2 + 3).
        self.assertEqual(sprint.dag_width(), 2)

    def test_overlap_warning_for_independent_overlapping_touches(self) -> None:
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": ["src/lib/foo.py"]},
            {"id": "slice-2", "title": "B", "type": "AFK", "depends_on": [], "touches": ["src/lib/**"]},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        sprint = load_sprint(path)
        warnings = sprint.overlap_warnings()
        self.assertEqual(len(warnings), 1)
        a, b, _glob = warnings[0]
        self.assertEqual({a, b}, {"slice-1", "slice-2"})

    def test_no_overlap_for_disjoint_touches(self) -> None:
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": ["src/a/**"]},
            {"id": "slice-2", "title": "B", "type": "AFK", "depends_on": [], "touches": ["src/b/**"]},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        sprint = load_sprint(path)
        self.assertEqual(sprint.overlap_warnings(), [])

    def test_no_overlap_warning_when_dependent(self) -> None:
        # If slice-2 depends on slice-1 they're not "independent" → no warning.
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": ["src/a/**"]},
            {"id": "slice-2", "title": "B", "type": "AFK", "depends_on": ["slice-1"], "touches": ["src/a/**"]},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        sprint = load_sprint(path)
        self.assertEqual(sprint.overlap_warnings(), [])


class FeedbackSprintTests(_TempSprintsMixin, unittest.TestCase):
    def test_loads_feedback_sprint_with_parent(self) -> None:
        path = _build_sprint(
            self._tmp,
            sprint_id="2026-05-20-feedback-1",
            kind="feedback",
            parent_sprint="2026-05-18-html-renderer",
        )
        sprint = load_sprint(path)
        self.assertEqual(sprint.kind, "feedback")
        self.assertEqual(sprint.parent_sprint, "2026-05-18-html-renderer")
        self.assertEqual(sprint.feedback, "- bullet one\n")

    def test_feedback_requires_parent_pointer(self) -> None:
        path = _build_sprint(
            self._tmp,
            sprint_id="2026-05-20-feedback-2",
            kind="feedback",
            parent_sprint="",
        )
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("parent_sprint", str(ctx.exception))

    def test_greenfield_rejects_parent_pointer(self) -> None:
        path = _build_sprint(
            self._tmp,
            sprint_id="2026-05-21-bad-greenfield",
            kind="greenfield",
            parent_sprint="2026-05-18-html-renderer",
        )
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("greenfield", str(ctx.exception))

    def test_feedback_missing_feedback_md_rejected(self) -> None:
        path = _build_sprint(
            self._tmp,
            sprint_id="2026-05-20-feedback-3",
            kind="feedback",
            parent_sprint="2026-05-18-html-renderer",
        )
        (path / "feedback.md").unlink()
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("feedback.md", str(ctx.exception))


class SprintValidationTests(_TempSprintsMixin, unittest.TestCase):
    def test_rejects_cycle(self) -> None:
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": ["slice-2"], "touches": []},
            {"id": "slice-2", "title": "B", "type": "AFK", "depends_on": ["slice-1"], "touches": []},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_rejects_self_dependency(self) -> None:
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": ["slice-1"], "touches": []},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("itself", str(ctx.exception))

    def test_rejects_missing_dependency_target(self) -> None:
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": ["nope"], "touches": []},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("unknown slice", str(ctx.exception))

    def test_rejects_duplicate_slice_ids(self) -> None:
        slices = [
            {"id": "slice-1", "title": "A", "type": "AFK", "depends_on": [], "touches": []},
            {"id": "slice-1", "title": "B", "type": "AFK", "depends_on": [], "touches": []},
        ]
        # Both have prompt "slices/slice-1.md" — same file; that's fine, only id matters.
        path = _build_sprint(self._tmp, slices=slices)
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("duplicate slice id", str(ctx.exception))

    def test_rejects_unknown_slice_type(self) -> None:
        slices = [
            {"id": "slice-1", "title": "A", "type": "OTHER", "depends_on": [], "touches": []},
        ]
        path = _build_sprint(self._tmp, slices=slices)
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("type", str(ctx.exception))

    def test_rejects_missing_overview(self) -> None:
        path = _build_sprint(self._tmp)
        (path / "overview.md").unlink()
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("overview.md", str(ctx.exception))

    def test_rejects_missing_manifest(self) -> None:
        path = _build_sprint(self._tmp)
        (path / "manifest.toml").unlink()
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("manifest", str(ctx.exception))

    def test_rejects_malformed_manifest_toml(self) -> None:
        path = _build_sprint(self._tmp)
        (path / "manifest.toml").write_text("this is = not = toml\n")
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("malformed", str(ctx.exception))

    def test_rejects_missing_target_repo(self) -> None:
        path = _build_sprint(self._tmp)
        # Strip [sprint].target_repo line.
        manifest = (path / "manifest.toml").read_text()
        manifest = "\n".join(
            line for line in manifest.splitlines() if "target_repo" not in line
        )
        (path / "manifest.toml").write_text(manifest)
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        self.assertIn("target_repo", str(ctx.exception))

    def test_rejects_prompt_path_escape(self) -> None:
        slices = [
            {
                "id": "slice-1",
                "title": "Bad",
                "type": "AFK",
                "depends_on": [],
                "touches": [],
                "prompt": "../../etc/passwd",
            },
        ]
        path = _build_sprint(self._tmp, slices=slices)
        with self.assertRaises(SprintError) as ctx:
            load_sprint(path)
        msg = str(ctx.exception)
        self.assertTrue("escape" in msg or "missing" in msg)


class ListSprintsTests(_TempSprintsMixin, unittest.TestCase):
    def test_lists_sprint_directories_in_order(self) -> None:
        _build_sprint(self._tmp, sprint_id="2026-05-18-first")
        _build_sprint(self._tmp, sprint_id="2026-05-19-second")
        _build_sprint(self._tmp, sprint_id="2026-05-20-third")
        # Add a non-sprint directory to ensure it's skipped.
        (self._tmp / "not-a-sprint").mkdir()
        ids = list_sprints(self._tmp)
        self.assertEqual(ids, [
            "2026-05-18-first",
            "2026-05-19-second",
            "2026-05-20-third",
        ])

    def test_missing_dir_returns_empty(self) -> None:
        self.assertEqual(list_sprints(self._tmp / "does-not-exist"), [])


if __name__ == "__main__":
    unittest.main()
