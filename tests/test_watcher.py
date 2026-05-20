"""Tests for naml.watcher — JSONL tailing + cold-start replay.

Covers slice-10's acceptance criteria for the watcher:

- ``replay_all`` rebuilds aggregates from disk correctly.
- Cold-start replay completes in < 2s for the size of dataset spec.md
  calls out (a 6-month-old project ~ 200 files).
- ``_read_new_lines`` returns only complete lines and advances the
  byte cursor accordingly.
- The cursor file ``state-cursor.json`` round-trips.
- The full TokensWatcher fires within 200ms of a JSONL append (only
  if ``watchdog`` is installed; skipped otherwise).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from naml.aggregator import Aggregator
from naml.watcher import (
    CURSOR_FILENAME,
    TokensWatcher,
    _load_cursor,
    _read_new_lines,
    _save_cursor,
    cold_start_then_watch,
    cursor_path_for,
    replay_all,
)


def _event_line(
    *,
    slice_id: str,
    when: datetime,
    cost: float = 0.01,
    tokens_in: int = 100,
) -> str:
    """Produce one JSONL line in the schema documented in spec Q8b."""
    payload = {
        "t": when.astimezone(timezone.utc).isoformat(),
        "slice": slice_id,
        "session": "sess",
        "turn": 1,
        "tokens_in": tokens_in,
        "tokens_out": 50,
        "cache_read": 0,
        "cache_write": 0,
        "cost_usd": cost,
        "ctx_pct": 10,
    }
    return json.dumps(payload, separators=(",", ":"))


def _seed_sprint(
    sprints_root: Path,
    sprint_id: str,
    *,
    slices: dict[str, list[str]],
) -> dict[str, Path]:
    """Write ``state/<slice>.tokens.jsonl`` files for one sprint.

    Returns a dict ``{slice_id: path}`` for downstream assertions.
    """
    state = sprints_root / sprint_id / "state"
    state.mkdir(parents=True)
    out: dict[str, Path] = {}
    for slice_id, lines in slices.items():
        p = state / f"{slice_id}.tokens.jsonl"
        p.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        out[slice_id] = p
    return out


class _TempMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-watcher-")).resolve()
        self._sprints = self._tmp / ".naml" / "sprints"
        self._sprints.mkdir(parents=True)

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)


class ReadNewLinesTests(_TempMixin, unittest.TestCase):
    def test_reads_complete_lines_only(self) -> None:
        p = self._tmp / "x.jsonl"
        p.write_text("alpha\nbeta\npart", encoding="utf-8")

        lines, pos = _read_new_lines(p, 0)
        self.assertEqual(lines, ["alpha", "beta"])
        # Cursor stops at the newline after "beta\n" — "part" is left.
        self.assertEqual(pos, len(b"alpha\nbeta\n"))

    def test_empty_file_returns_empty(self) -> None:
        p = self._tmp / "x.jsonl"
        p.write_text("", encoding="utf-8")
        lines, pos = _read_new_lines(p, 0)
        self.assertEqual(lines, [])
        self.assertEqual(pos, 0)

    def test_resume_from_cursor(self) -> None:
        p = self._tmp / "x.jsonl"
        p.write_text("alpha\nbeta\n", encoding="utf-8")
        # Skip "alpha\n".
        lines, pos = _read_new_lines(p, len(b"alpha\n"))
        self.assertEqual(lines, ["beta"])
        self.assertEqual(pos, len(b"alpha\nbeta\n"))


class ReplayAllTests(_TempMixin, unittest.TestCase):
    def test_replay_aggregates_correctly(self) -> None:
        when = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        _seed_sprint(
            self._sprints,
            "sprint-A",
            slices={
                "slice-1": [
                    _event_line(slice_id="slice-1", when=when, cost=0.10),
                    _event_line(slice_id="slice-1", when=when, cost=0.20),
                ],
                "slice-2": [
                    _event_line(slice_id="slice-2", when=when, cost=0.30),
                ],
            },
        )
        _seed_sprint(
            self._sprints,
            "sprint-B",
            slices={
                "slice-3": [
                    _event_line(slice_id="slice-3", when=when, cost=0.40),
                ],
            },
        )

        agg = Aggregator()
        count, positions = replay_all(self._sprints, agg)
        self.assertEqual(count, 4)

        self.assertEqual(set(agg.per_slice.keys()), {"slice-1", "slice-2", "slice-3"})
        self.assertEqual(set(agg.per_sprint.keys()), {"sprint-A", "sprint-B"})
        # sprint-A = slice-1 (0.10 + 0.20) + slice-2 (0.30) = 0.60
        self.assertAlmostEqual(agg.per_sprint["sprint-A"].cost_usd, 0.60)
        self.assertAlmostEqual(agg.per_sprint["sprint-B"].cost_usd, 0.40)
        self.assertAlmostEqual(agg.per_project.lifetime.cost_usd, 1.00)

        # Positions point at the end of every file — so the watcher would
        # only read bytes appended *after* this replay.
        for path, offset in positions.items():
            self.assertEqual(offset, path.stat().st_size)

    def test_replay_skips_malformed_lines(self) -> None:
        when = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        _seed_sprint(
            self._sprints,
            "sprint-A",
            slices={
                "slice-1": [
                    _event_line(slice_id="slice-1", when=when, cost=0.10),
                    "not json at all",
                    "{broken",
                    _event_line(slice_id="slice-1", when=when, cost=0.05),
                ],
            },
        )
        agg = Aggregator()
        count, _ = replay_all(self._sprints, agg)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(agg.per_slice["slice-1"].cost_usd, 0.15)

    def test_replay_missing_root_is_safe(self) -> None:
        agg = Aggregator()
        count, positions = replay_all(self._tmp / "does-not-exist", agg)
        self.assertEqual(count, 0)
        self.assertEqual(positions, {})

    def test_cold_start_under_2s_for_simulated_six_month_project(self) -> None:
        """200 files × 30 events each ≈ 6k events, the order of magnitude
        spec.md cites for a 6-month-old project. Must replay under 2s."""
        when = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        for s in range(200):
            sprint_id = f"sprint-{s:03d}"
            state = self._sprints / sprint_id / "state"
            state.mkdir(parents=True)
            with (state / "slice-1.tokens.jsonl").open(
                "w", encoding="utf-8"
            ) as fh:
                for _ in range(30):
                    fh.write(
                        _event_line(slice_id=f"slice-{s:03d}", when=when)
                        + "\n"
                    )

        agg = Aggregator()
        started = time.perf_counter()
        count, _ = replay_all(self._sprints, agg)
        elapsed = time.perf_counter() - started

        self.assertEqual(count, 200 * 30)
        # Spec asks for < 2s. Be a little generous for CI noise but still
        # fail loudly if we accidentally slip into seconds territory.
        self.assertLess(elapsed, 2.0, f"cold start too slow: {elapsed:.2f}s")


class CursorRoundTripTests(_TempMixin, unittest.TestCase):
    def test_save_and_load_cursor(self) -> None:
        cursor = self._tmp / CURSOR_FILENAME
        positions = {self._tmp / "a.jsonl": 100, self._tmp / "b.jsonl": 200}
        _save_cursor(cursor, positions)

        loaded = _load_cursor(cursor)
        self.assertEqual(loaded.positions[str(self._tmp / "a.jsonl")], 100)
        self.assertEqual(loaded.positions[str(self._tmp / "b.jsonl")], 200)

    def test_missing_file_returns_empty_cursor(self) -> None:
        loaded = _load_cursor(self._tmp / "nope.json")
        self.assertEqual(loaded.positions, {})

    def test_malformed_cursor_is_tolerated(self) -> None:
        cursor = self._tmp / CURSOR_FILENAME
        cursor.write_text("{not valid json", encoding="utf-8")
        loaded = _load_cursor(cursor)
        self.assertEqual(loaded.positions, {})

    def test_cursor_path_helper(self) -> None:
        self.assertEqual(
            cursor_path_for(self._tmp / ".naml"),
            self._tmp / ".naml" / CURSOR_FILENAME,
        )


class WatcherNotifyTests(_TempMixin, unittest.TestCase):
    """Drive the watcher's notify path without booting a real Observer.

    This exercises the debounce + drain + cursor-save logic deterministically.
    """

    def test_drain_consumes_new_lines_and_advances_cursor(self) -> None:
        agg = Aggregator()
        cursor = self._tmp / ".naml" / CURSOR_FILENAME
        watcher = TokensWatcher(
            self._sprints,
            agg,
            cursor_path=cursor,
            debounce_seconds=0.0,  # inline drain for determinism
        )

        when = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        paths = _seed_sprint(
            self._sprints,
            "sprint-A",
            slices={"slice-1": [_event_line(slice_id="slice-1", when=when)]},
        )
        # Seed positions as if cold-start had pre-counted the file's bytes,
        # then append a new line and notify.
        watcher.set_positions({paths["slice-1"]: paths["slice-1"].stat().st_size})

        with paths["slice-1"].open("a", encoding="utf-8") as fh:
            fh.write(_event_line(slice_id="slice-1", when=when, cost=0.99) + "\n")

        watcher.notify_modified(paths["slice-1"])
        watcher.force_flush()

        self.assertIn("slice-1", agg.per_slice)
        self.assertAlmostEqual(agg.per_slice["slice-1"].cost_usd, 0.99)
        # Cursor file must have been written.
        self.assertTrue(cursor.is_file())

    def test_partial_line_left_for_next_drain(self) -> None:
        agg = Aggregator()
        watcher = TokensWatcher(
            self._sprints, agg, cursor_path=None, debounce_seconds=0.0,
        )

        state = self._sprints / "sprint-A" / "state"
        state.mkdir(parents=True)
        jsonl = state / "slice-1.tokens.jsonl"
        jsonl.write_text("", encoding="utf-8")

        # Write the first half of a line (no newline) — must NOT be parsed.
        when = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        first_half = _event_line(slice_id="slice-1", when=when)[:30]
        with jsonl.open("a", encoding="utf-8") as fh:
            fh.write(first_half)
        watcher.notify_modified(jsonl)
        watcher.force_flush()
        self.assertEqual(agg.per_slice, {})

        # Finish the line; now we expect exactly one apply.
        rest = _event_line(slice_id="slice-1", when=when)[30:] + "\n"
        with jsonl.open("a", encoding="utf-8") as fh:
            fh.write(rest)
        watcher.notify_modified(jsonl)
        watcher.force_flush()
        self.assertIn("slice-1", agg.per_slice)
        self.assertEqual(agg.per_slice["slice-1"].turns, 1)


class WatcherDebounceTests(_TempMixin, unittest.TestCase):
    """The debounce should coalesce a burst of notifications into one drain."""

    def test_burst_of_notifications_coalesce_into_one_drain(self) -> None:
        agg = Aggregator()
        drain_calls: list[int] = []

        def on_drain(events) -> None:
            drain_calls.append(len(events))

        watcher = TokensWatcher(
            self._sprints,
            agg,
            cursor_path=None,
            debounce_seconds=0.05,
            on_drain=on_drain,
        )

        when = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
        paths = _seed_sprint(
            self._sprints,
            "sprint-A",
            slices={"slice-1": []},
        )
        watcher.set_positions({paths["slice-1"]: 0})

        # Append 5 lines in quick succession with 5 notify calls.
        with paths["slice-1"].open("a", encoding="utf-8") as fh:
            for i in range(5):
                fh.write(
                    _event_line(slice_id="slice-1", when=when, cost=0.01) + "\n"
                )
        for _ in range(5):
            watcher.notify_modified(paths["slice-1"])

        # Wait long enough for the debounce timer to fire.
        time.sleep(0.2)

        # All 5 events applied; only one drain should have fired thanks to
        # the debounce coalescing.
        self.assertEqual(agg.per_slice["slice-1"].turns, 5)
        self.assertEqual(len(drain_calls), 1)
        self.assertEqual(drain_calls[0], 5)


def _has_watchdog() -> bool:
    try:
        import watchdog  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_watchdog(), "watchdog not installed")
class WatcherIntegrationTests(_TempMixin, unittest.TestCase):
    """Real-Observer integration test. Verifies the 200ms acceptance bar."""

    def test_appended_line_drains_within_200ms(self) -> None:
        agg = Aggregator()
        cursor = self._tmp / ".naml" / CURSOR_FILENAME
        watcher, count, _replay_time = cold_start_then_watch(
            self._sprints,
            agg,
            naml_dir=self._tmp / ".naml",
            debounce_seconds=0.05,
        )
        self.assertEqual(count, 0)

        # Prepare an empty JSONL file BEFORE start so the Observer picks it
        # up as a target.
        state = self._sprints / "sprint-A" / "state"
        state.mkdir(parents=True)
        jsonl = state / "slice-1.tokens.jsonl"
        jsonl.touch()
        # Position seeded at zero (file is empty).
        watcher.set_positions({jsonl: 0})

        try:
            watcher.start()
            # Some FS observers need a brief moment to register their watch.
            time.sleep(0.05)

            when = datetime(2026, 5, 19, 14, 0, 0, tzinfo=timezone.utc)
            line = _event_line(slice_id="slice-1", when=when, cost=0.42)
            with jsonl.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())

            # Poll until applied OR 1s elapsed. The acceptance bar is 200ms;
            # we give 1s of headroom for slow CI but still assert the 200ms
            # claim with the first deadline check below.
            start_t = time.perf_counter()
            deadline = start_t + 1.0
            observed_within_200ms = False
            while time.perf_counter() < deadline:
                if "slice-1" in agg.per_slice:
                    if (time.perf_counter() - start_t) < 0.20:
                        observed_within_200ms = True
                    break
                time.sleep(0.01)

            self.assertIn(
                "slice-1", agg.per_slice,
                "watcher did not apply the appended line within 1s",
            )
            self.assertTrue(
                observed_within_200ms,
                f"watcher took > 200ms to apply (elapsed "
                f"{(time.perf_counter() - start_t) * 1000:.0f}ms)",
            )
        finally:
            watcher.stop()


if __name__ == "__main__":
    unittest.main()
