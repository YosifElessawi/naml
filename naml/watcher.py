"""File watcher that streams new JSONL events into the aggregator.

The cockpit's data pipeline:

    Claude --output-format stream-json
      → lane parses usage block per turn
      → append one line to state/<slice>.tokens.jsonl
      → watchdog (FSEvents on macOS / inotify on Linux) fires
      → this module reads new lines from the file tail
      → aggregator.apply_event(...)
      → SSE metric-tick (slice-11)

Behaviour the spec pins down (``artifacts/spec.md`` Q8b):

- Watches ``.naml/sprints/*/state/*.tokens.jsonl``. Each file's sprint
  is the parent directory of its ``state/`` dir.
- Tracks a per-file byte position so re-reads only see new bytes.
- Debounces rapid writes (50ms coalesce) so a burst of appends becomes
  one drain.
- Persists positions to ``.naml/state-cursor.json`` after each drain so
  the cursor survives a server restart. (Cold start still does a full
  replay; the cursor is the safety net for a future "warm restart"
  path and an operator-visible record of where we are.)

The watcher does NOT push events out over SSE — that's slice-11. Here
we only fan events into the aggregator.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .aggregator import Aggregator


log = logging.getLogger("naml.watcher")


CURSOR_FILENAME = "state-cursor.json"
DEFAULT_DEBOUNCE_SECONDS = 0.05  # 50ms — spec Q8b
TOKENS_SUFFIX = ".tokens.jsonl"


def _sprint_id_for(path: Path) -> str:
    """Given ``…/.naml/sprints/<sprint-id>/state/<slice>.tokens.jsonl``,
    pull out ``<sprint-id>``. Returns ``""`` if the layout doesn't match."""
    try:
        # path.parent == .../state/
        # path.parent.parent == .../<sprint-id>/
        return path.parent.parent.name
    except (IndexError, ValueError):
        return ""


@dataclass
class _CursorState:
    """In-memory shape of ``state-cursor.json``."""

    positions: dict[str, int]  # absolute path → byte offset

    def to_dict(self) -> dict[str, object]:
        return {"version": 1, "positions": dict(self.positions)}


def _load_cursor(cursor_path: Path) -> _CursorState:
    """Read a previously-written cursor file. Tolerant of malformed JSON."""
    if not cursor_path.is_file():
        return _CursorState(positions={})
    try:
        raw = json.loads(cursor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _CursorState(positions={})
    if not isinstance(raw, dict):
        return _CursorState(positions={})
    pos_raw = raw.get("positions")
    if not isinstance(pos_raw, dict):
        return _CursorState(positions={})
    positions: dict[str, int] = {}
    for k, v in pos_raw.items():
        if isinstance(k, str) and isinstance(v, int) and v >= 0:
            positions[k] = v
    return _CursorState(positions=positions)


def _save_cursor(cursor_path: Path, positions: dict[Path, int]) -> None:
    """Atomic write of the cursor file (tmp + rename)."""
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "positions": {str(p): int(off) for p, off in positions.items()},
    }
    tmp = cursor_path.with_name(cursor_path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(cursor_path)


def _read_new_lines(path: Path, start: int) -> tuple[list[str], int]:
    """Read bytes from ``start`` to EOF, split into lines. Returns
    ``(complete_lines, new_position)``.

    A trailing partial line (no terminating ``\\n``) is left for the next
    drain so we never feed a half-record to the aggregator.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            chunk = fh.read()
    except OSError:
        return [], start

    if not chunk:
        return [], start

    # Find the last newline; anything after it is a partial line we leave on
    # disk for next time.
    last_nl = chunk.rfind(b"\n")
    if last_nl == -1:
        # No complete lines yet — keep cursor where it was.
        return [], start

    complete = chunk[: last_nl + 1]
    consumed = last_nl + 1
    try:
        text = complete.decode("utf-8")
    except UnicodeDecodeError:
        # Drop garbage; advance the cursor so we don't loop on it.
        return [], start + consumed

    lines = [ln for ln in text.split("\n") if ln]
    return lines, start + consumed


def replay_all(
    sprints_root: Path, aggregator: Aggregator
) -> tuple[int, dict[Path, int]]:
    """Scan every ``*.tokens.jsonl`` under ``sprints_root`` and replay
    each line into ``aggregator``.

    Returns ``(event_count, positions)`` so the caller can hand
    ``positions`` to the watcher (so the watcher only reads bytes that
    arrive *after* cold-start).
    """
    count = 0
    positions: dict[Path, int] = {}
    if not sprints_root.is_dir():
        return count, positions

    for jsonl in sorted(sprints_root.glob(f"*/state/*{TOKENS_SUFFIX}")):
        sprint_id = _sprint_id_for(jsonl)
        try:
            text = jsonl.read_text(encoding="utf-8")
        except OSError:
            continue
        # Record the byte offset of the end-of-file so the watcher resumes
        # from here, not from the beginning.
        positions[jsonl] = len(text.encode("utf-8"))
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines silently — the lane already logs the
                # original parse failure when it tried to emit.
                continue
            if aggregator.apply_event(ev, sprint_id=sprint_id):
                count += 1
    return count, positions


class TokensWatcher:
    """Watch ``*.tokens.jsonl`` under a sprints root and feed an aggregator.

    Concurrency model:

    - The ``watchdog`` Observer runs its own thread and calls our event
      handler from there.
    - The handler does NOT do work inline; it adds the path to a pending
      set and (re)schedules a Timer to drain after ``debounce_seconds``.
    - The Timer callback acquires a single lock, drains the pending set,
      reads new bytes, calls ``aggregator.apply_event`` for each line,
      and rewrites the cursor file.

    The result is one I/O burst per ~50ms regardless of how many lines
    Claude appended in that window.
    """

    def __init__(
        self,
        sprints_root: Path,
        aggregator: Aggregator,
        *,
        cursor_path: Path | None = None,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        on_drain: Callable[[list[dict]], None] | None = None,
    ) -> None:
        self.sprints_root = Path(sprints_root).resolve()
        self.aggregator = aggregator
        self.cursor_path = cursor_path
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self._on_drain = on_drain

        self._positions: dict[Path, int] = {}
        self._pending: set[Path] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._observer = None  # type: ignore[var-annotated]
        self._started = False

    # --- lifecycle ---------------------------------------------------

    def set_positions(self, positions: dict[Path, int]) -> None:
        """Seed the byte cursor map (called after cold-start replay)."""
        with self._lock:
            self._positions = {Path(p): int(off) for p, off in positions.items()}

    def start(self) -> None:
        """Boot the watchdog Observer thread.

        Watchdog is a runtime dep, but importing it lazily means the rest of
        ``naml.watcher`` (replay_all, _read_new_lines, cursor helpers) stays
        importable in environments where watchdog isn't installed yet — e.g.
        the aggregator tests don't need it.
        """
        if self._started:
            return
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        watcher = self

        class _Handler(FileSystemEventHandler):
            """Defer all file-system events to the parent watcher's
            debounced notify queue."""

            def _push(self, event) -> None:  # type: ignore[no-untyped-def]
                if getattr(event, "is_directory", False):
                    return
                src = getattr(event, "src_path", None)
                if not isinstance(src, str):
                    return
                path = Path(src)
                if not path.name.endswith(TOKENS_SUFFIX):
                    return
                watcher.notify_modified(path)

            def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
                self._push(event)

            def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
                self._push(event)

            def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
                # A move to a watched name (e.g. atomic rename of a partial
                # tokens file into place) still counts as new bytes.
                if hasattr(event, "dest_path") and isinstance(event.dest_path, str):
                    path = Path(event.dest_path)
                    if path.name.endswith(TOKENS_SUFFIX):
                        watcher.notify_modified(path)

        self._observer = Observer()
        self.sprints_root.mkdir(parents=True, exist_ok=True)
        self._observer.schedule(_Handler(), str(self.sprints_root), recursive=True)
        self._observer.start()
        self._started = True
        log.info("naml.watcher: observing %s", self.sprints_root)

    def stop(self) -> None:
        if not self._started:
            return
        try:
            if self._observer is not None:
                self._observer.stop()
                self._observer.join(timeout=2.0)
        finally:
            self._started = False
        # Drain anything pending so the test / shutdown path observes a
        # consistent end state.
        self._cancel_timer()
        self._flush()

    # --- event ingestion --------------------------------------------

    def notify_modified(self, path: Path) -> None:
        """Called by the watchdog handler when a tokens.jsonl is touched.

        Public so tests can drive the watcher without a real Observer.
        """
        if not path.name.endswith(TOKENS_SUFFIX):
            return
        with self._lock:
            self._pending.add(path)
            self._schedule_locked()

    def force_flush(self) -> None:
        """Drain pending paths synchronously. Used in tests and at shutdown."""
        self._cancel_timer()
        self._flush()

    # --- internals --------------------------------------------------

    def _schedule_locked(self) -> None:
        """Caller holds ``self._lock``."""
        if self._timer is not None and self._timer.is_alive():
            return
        if self.debounce_seconds <= 0:
            # Drain inline — used by tests that want determinism.
            timer = threading.Timer(0.0, self._flush)
        else:
            timer = threading.Timer(self.debounce_seconds, self._flush)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _cancel_timer(self) -> None:
        with self._lock:
            t = self._timer
            self._timer = None
        if t is not None:
            t.cancel()

    def _flush(self) -> None:
        with self._lock:
            paths = list(self._pending)
            self._pending.clear()
            self._timer = None
            positions = dict(self._positions)

        if not paths:
            return

        applied: list[dict] = []
        new_positions: dict[Path, int] = {}
        for raw_path in paths:
            path = Path(raw_path)
            start = positions.get(path, 0)
            lines, new_pos = _read_new_lines(path, start)
            if new_pos != start:
                new_positions[path] = new_pos
            if not lines:
                continue
            sprint_id = _sprint_id_for(path)
            for line in lines:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if self.aggregator.apply_event(ev, sprint_id=sprint_id):
                    applied.append(ev)

        if new_positions:
            with self._lock:
                self._positions.update(new_positions)
                snapshot_positions = dict(self._positions)
            if self.cursor_path is not None:
                try:
                    _save_cursor(self.cursor_path, snapshot_positions)
                except OSError as exc:
                    log.warning("naml.watcher: could not write cursor: %s", exc)

        if applied and self._on_drain is not None:
            try:
                self._on_drain(applied)
            except Exception:  # noqa: BLE001 — best-effort SSE side channel
                log.exception("naml.watcher: on_drain callback failed")


def cursor_path_for(naml_dir: Path) -> Path:
    """Canonical location for the cursor file."""
    return Path(naml_dir) / CURSOR_FILENAME


def cold_start_then_watch(
    sprints_root: Path,
    aggregator: Aggregator,
    *,
    naml_dir: Path,
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    on_drain: Callable[[list[dict]], None] | None = None,
) -> tuple["TokensWatcher", int, float]:
    """Replay everything in ``sprints_root`` into ``aggregator``, then
    boot a ``TokensWatcher`` seeded with the post-replay byte positions.

    Returns ``(watcher, event_count, replay_seconds)`` so the caller can
    log "cold start replay: N events in Tms" per the spec.
    """
    started = time.perf_counter()
    count, positions = replay_all(sprints_root, aggregator)
    elapsed = time.perf_counter() - started

    watcher = TokensWatcher(
        sprints_root,
        aggregator,
        cursor_path=cursor_path_for(naml_dir),
        debounce_seconds=debounce_seconds,
        on_drain=on_drain,
    )
    watcher.set_positions(positions)
    return watcher, count, elapsed
