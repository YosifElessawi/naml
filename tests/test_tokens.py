"""Tests for naml.tokens — per-turn stream-json parsing + JSONL emission.

Covers the slice-9 acceptance criteria:
- Parsing a captured stream-json fixture produces the right JSONL lines.
- Appending is atomic (concurrent writers don't interleave).
- A malformed JSON line is logged + skipped, doesn't crash the lane.
- Context % handles missing ``cache_read`` gracefully.

And a few invariants the rest of the cockpit pipeline relies on:
- JSONL schema matches ``artifacts/spec.md`` field-for-field.
- Model picked up from a system:init event flows into cost_usd.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from naml.tokens import (
    TurnEmitter,
    TurnUsage,
    compute_cost_usd,
    compute_ctx_pct,
    parse_usage,
)


# A miniature stream-json transcript with two assistant turns and a final
# result event. Mirrors the shape Claude Code emits in real logs: each
# assistant event carries usage under ``message``, not at the top level.
_TURN1_USAGE = {
    "input_tokens": 100,
    "output_tokens": 250,
    "cache_read_input_tokens": 50_000,
    "cache_creation_input_tokens": 10_000,
}
_TURN2_USAGE = {
    "input_tokens": 200,
    "output_tokens": 480,
    "cache_read_input_tokens": 98_000,
    "cache_creation_input_tokens": 0,
}
_FIXTURE_LINES: list[str] = [
    json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-4-7"}),
    json.dumps({"type": "assistant", "message": {"usage": _TURN1_USAGE}}),
    json.dumps({"type": "user", "message": {"content": []}}),
    json.dumps({"type": "assistant", "message": {"usage": _TURN2_USAGE}}),
    json.dumps({"type": "result", "is_error": False, "total_cost_usd": 0.42}),
]


class _TempMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = Path(tempfile.mkdtemp(prefix="naml-tokens-")).resolve()

    def tearDown(self) -> None:  # type: ignore[override]
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _read_jsonl(self, path: Path) -> list[dict]:
        lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]


class ParseUsageTests(unittest.TestCase):
    def test_parses_all_fields(self) -> None:
        usage = parse_usage(_TURN1_USAGE)
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage.tokens_in, 100)
        self.assertEqual(usage.tokens_out, 250)
        self.assertEqual(usage.cache_read, 50_000)
        self.assertEqual(usage.cache_write, 10_000)

    def test_missing_fields_default_to_zero(self) -> None:
        usage = parse_usage({"input_tokens": 10})
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage.tokens_in, 10)
        self.assertEqual(usage.tokens_out, 0)
        self.assertEqual(usage.cache_read, 0)
        self.assertEqual(usage.cache_write, 0)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(parse_usage(None))

    def test_garbage_value_returns_none(self) -> None:
        self.assertIsNone(parse_usage("not a dict"))

    def test_non_numeric_field_returns_none(self) -> None:
        self.assertIsNone(parse_usage({"input_tokens": "huge"}))


class ContextPercentTests(unittest.TestCase):
    def test_full_usage(self) -> None:
        u = TurnUsage(tokens_in=100, tokens_out=0, cache_read=99_900, cache_write=0)
        self.assertEqual(compute_ctx_pct(u, 200_000), 50)

    def test_missing_cache_read(self) -> None:
        """The acceptance criterion: ctx % must not blow up without cache_read."""
        u = TurnUsage(tokens_in=2_000, tokens_out=0, cache_read=0, cache_write=0)
        self.assertEqual(compute_ctx_pct(u, 200_000), 1)

    def test_clamped_to_100(self) -> None:
        u = TurnUsage(tokens_in=10_000_000, tokens_out=0, cache_read=0, cache_write=0)
        self.assertEqual(compute_ctx_pct(u, 200_000), 100)

    def test_zero_max_returns_zero(self) -> None:
        u = TurnUsage(tokens_in=100, tokens_out=0, cache_read=0, cache_write=0)
        self.assertEqual(compute_ctx_pct(u, 0), 0)


class CostUsdTests(unittest.TestCase):
    def test_known_model(self) -> None:
        u = TurnUsage(
            tokens_in=1_000_000,
            tokens_out=0,
            cache_read=0,
            cache_write=0,
        )
        # Opus input is $15 / M tokens → $15 for 1M input tokens.
        self.assertAlmostEqual(compute_cost_usd(u, "claude-opus-4-7"), 15.0, places=2)

    def test_normalises_context_window_suffix(self) -> None:
        """Claude Code stamps ``[1m]`` onto the model id when using extended ctx."""
        u = TurnUsage(tokens_in=1_000_000, tokens_out=0, cache_read=0, cache_write=0)
        self.assertAlmostEqual(
            compute_cost_usd(u, "claude-opus-4-7[1m]"), 15.0, places=2
        )

    def test_unknown_model_is_zero(self) -> None:
        u = TurnUsage(tokens_in=1_000_000, tokens_out=0, cache_read=0, cache_write=0)
        self.assertEqual(compute_cost_usd(u, "claude-mystery-9000"), 0.0)


class EmitterFromFixtureTests(_TempMixin, unittest.TestCase):
    """End-to-end: feeding the captured stream-json fixture lands the right
    JSONL on disk. This is the headline acceptance criterion for slice-9."""

    def test_two_assistant_events_emit_two_jsonl_lines(self) -> None:
        out = self._tmp / "slice-1.tokens.jsonl"
        emitter = TurnEmitter(
            jsonl_path=out,
            slice_id="slice-1",
            session_id="abc12345",
            model_context_max=200_000,
        )
        for raw in _FIXTURE_LINES:
            emitter.feed_line(raw)

        records = self._read_jsonl(out)
        self.assertEqual(len(records), 2)

    def test_jsonl_schema_matches_spec(self) -> None:
        out = self._tmp / "slice-4.tokens.jsonl"
        emitter = TurnEmitter(
            jsonl_path=out,
            slice_id="slice-4",
            session_id="9b0cef13",
            model_context_max=200_000,
        )
        for raw in _FIXTURE_LINES:
            emitter.feed_line(raw)

        records = self._read_jsonl(out)
        first = records[0]
        expected_keys = {
            "t", "slice", "session", "turn",
            "tokens_in", "tokens_out", "cache_read", "cache_write",
            "cost_usd", "ctx_pct",
        }
        self.assertEqual(set(first.keys()), expected_keys)
        self.assertEqual(first["slice"], "slice-4")
        self.assertEqual(first["session"], "9b0cef13")
        self.assertEqual(first["turn"], 1)
        self.assertEqual(first["tokens_in"], _TURN1_USAGE["input_tokens"])
        self.assertEqual(first["tokens_out"], _TURN1_USAGE["output_tokens"])
        self.assertEqual(first["cache_read"], _TURN1_USAGE["cache_read_input_tokens"])
        self.assertEqual(first["cache_write"],
                         _TURN1_USAGE["cache_creation_input_tokens"])
        # ctx_pct = (100 + 50000) / 200000 = 25%
        self.assertEqual(first["ctx_pct"], 25)
        # Cost picked up the init-event model.
        self.assertGreater(first["cost_usd"], 0.0)

    def test_turn_counter_advances(self) -> None:
        out = self._tmp / "slice-1.tokens.jsonl"
        emitter = TurnEmitter(
            jsonl_path=out,
            slice_id="slice-1",
            session_id="s",
            model_context_max=200_000,
        )
        for raw in _FIXTURE_LINES:
            emitter.feed_line(raw)
        records = self._read_jsonl(out)
        self.assertEqual([r["turn"] for r in records], [1, 2])

    def test_top_level_usage_also_works(self) -> None:
        """A future Claude version emitting usage at top level still parses."""
        out = self._tmp / "slice-1.tokens.jsonl"
        emitter = TurnEmitter(
            jsonl_path=out,
            slice_id="slice-1",
            session_id="s",
            model_context_max=200_000,
        )
        line = json.dumps({"type": "assistant", "usage": _TURN1_USAGE})
        self.assertTrue(emitter.feed_line(line))
        records = self._read_jsonl(out)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tokens_in"], 100)


class MalformedLineTests(_TempMixin, unittest.TestCase):
    def test_malformed_json_does_not_crash_and_does_not_emit(self) -> None:
        out = self._tmp / "slice-1.tokens.jsonl"
        emitter = TurnEmitter(
            jsonl_path=out,
            slice_id="slice-1",
            session_id="s",
            model_context_max=200_000,
        )
        # Feed garbage; emitter must absorb without raising.
        self.assertFalse(emitter.feed_line("not json at all\n"))
        self.assertFalse(emitter.feed_line('{"broken": '))  # truncated JSON
        # The lane's log file may contain banner lines that aren't JSON; those
        # also have to be no-ops.
        self.assertFalse(emitter.feed_line("====\nCLAUDE START (cap 30m)\n===\n"))
        # Empty line: no-op.
        self.assertFalse(emitter.feed_line(""))
        self.assertFalse(emitter.feed_line("\n"))
        # No file written when nothing emitted.
        self.assertFalse(out.exists())

    def test_event_without_type_is_ignored(self) -> None:
        out = self._tmp / "slice-1.tokens.jsonl"
        emitter = TurnEmitter(
            jsonl_path=out,
            slice_id="slice-1",
            session_id="s",
            model_context_max=200_000,
        )
        self.assertFalse(emitter.feed_line(json.dumps({"foo": "bar"})))

    def test_non_assistant_event_is_ignored(self) -> None:
        out = self._tmp / "slice-1.tokens.jsonl"
        emitter = TurnEmitter(
            jsonl_path=out,
            slice_id="slice-1",
            session_id="s",
            model_context_max=200_000,
        )
        self.assertFalse(emitter.feed_line(
            json.dumps({"type": "user", "message": {"usage": _TURN1_USAGE}})
        ))


class ConcurrentAppendTests(_TempMixin, unittest.TestCase):
    """Two threads emitting at the same time must not interleave bytes.

    POSIX guarantees writes ≤ PIPE_BUF bytes are atomic under O_APPEND, and
    each JSONL record is well below that. We assert post-hoc by replaying
    the file: every line must round-trip through ``json.loads``.
    """

    def test_two_emitters_dont_interleave(self) -> None:
        out = self._tmp / "slice-1.tokens.jsonl"
        # Both emitters point at the SAME path — simulating two writers.
        ems = [
            TurnEmitter(
                jsonl_path=out,
                slice_id="slice-1",
                session_id=f"sess-{i}",
                model_context_max=200_000,
            )
            for i in range(2)
        ]
        event = json.dumps({"type": "assistant", "message": {"usage": _TURN1_USAGE}})

        def worker(em: TurnEmitter) -> None:
            for _ in range(200):
                em.feed_line(event)

        threads = [threading.Thread(target=worker, args=(em,)) for em in ems]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 2 emitters × 200 events each. Every line must be valid JSON.
        records = self._read_jsonl(out)
        self.assertEqual(len(records), 400)
        for r in records:
            # Schema is intact on every record.
            self.assertEqual(r["tokens_in"], _TURN1_USAGE["input_tokens"])


class CompletionDetectionStillWorksTests(_TempMixin, unittest.TestCase):
    """``_scan_for_success_event`` reads the same log lines we now tee
    through the emitter. The acceptance criterion ('existing completion
    detection still works') is asserted by piping the fixture through both."""

    def test_scanner_sees_result_event(self) -> None:
        from naml.claude import _scan_for_success_event

        out = self._tmp / "slice-1.tokens.jsonl"
        log = self._tmp / "agent.log"
        emitter = TurnEmitter(
            jsonl_path=out,
            slice_id="slice-1",
            session_id="s",
            model_context_max=200_000,
        )
        # Write fixture to the log (simulating what the tee thread does).
        with log.open("a", encoding="utf-8") as fh:
            for raw in _FIXTURE_LINES:
                fh.write(raw + "\n")
                emitter.feed_line(raw)

        # The completion scanner must still find the result event.
        found, offset = _scan_for_success_event(log, 0, 0)
        self.assertTrue(found)
        self.assertGreater(offset, 0)
        # And the JSONL must have both turns recorded.
        self.assertEqual(len(self._read_jsonl(out)), 2)


if __name__ == "__main__":
    unittest.main()
