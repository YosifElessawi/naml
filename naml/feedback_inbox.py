"""Parse ``docs/feedback/inbox.md`` for the cockpit Dashboard sidecar.

The file is editable on disk; the cockpit polls this every 30s. The format
is intentionally loose — a list of bullets, optionally annotated with a
source line ``(from <where>)`` and an ISO date. Every bullet under any
heading other than ``## Filed`` (case-insensitive) counts as *unfiled*.

The parser keeps zero external deps so it runs on stdlib Python.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Capture: `- text` or `* text` with optional trailing `(from …)` annotation
# and optional inline ISO date. The captured-group structure is:
#   1 = bullet text (raw, may contain the from-annotation)
_BULLET_RE = re.compile(r"^[ \t]*[-*][ \t]+(?P<text>.+?)\s*$")
_FROM_RE = re.compile(r"\(from[: ]+(?P<src>[^)]+)\)\s*$", re.IGNORECASE)
_ISO_RE = re.compile(r"(?P<iso>\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z?)?)")
# Leading `(2026-05-19)` annotation that lets users tag the date manually.
_DATE_PREFIX_RE = re.compile(
    r"^\((?P<iso>\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z?)?)\)\s*"
)
_HEADING_RE = re.compile(r"^#+\s+(?P<title>.+?)\s*$")

_FILED_HEADINGS = {"filed", "archived", "done"}


@dataclass(frozen=True)
class FeedbackBullet:
    id: str
    text: str
    source: str
    added_at: str | None


def parse_inbox(text: str) -> list[FeedbackBullet]:
    """Parse the inbox markdown into a list of unfiled bullets, in source
    order. Filed-section bullets are skipped.
    """
    bullets: list[FeedbackBullet] = []
    section: str = "(unfiled)"
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        heading = _HEADING_RE.match(line)
        if heading:
            section = heading.group("title").strip()
            continue
        bullet = _BULLET_RE.match(line)
        if not bullet:
            continue
        if section.lower() in _FILED_HEADINGS:
            continue
        body = bullet.group("text").strip()
        from_m = _FROM_RE.search(body)
        if from_m:
            source = from_m.group("src").strip()
            body = _FROM_RE.sub("", body).strip()
        else:
            source = section
        added_at: str | None = None
        prefix_m = _DATE_PREFIX_RE.match(body)
        if prefix_m:
            added_at = prefix_m.group("iso")
            body = _DATE_PREFIX_RE.sub("", body, count=1).strip()
        else:
            iso_m = _ISO_RE.search(body)
            if iso_m:
                added_at = iso_m.group("iso")
        # Stable id derived from text + source so dedupe across polls works.
        digest = hashlib.sha1(f"{source}|{body}".encode("utf-8")).hexdigest()[:10]
        bullets.append(
            FeedbackBullet(
                id=f"fb-{digest}",
                text=body,
                source=source,
                added_at=added_at,
            )
        )
    return bullets


def load_inbox(path: Path) -> list[FeedbackBullet]:
    """Read + parse the inbox file. Missing or unreadable → empty list."""
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return []
    except OSError:
        return []
    return parse_inbox(text)


def build_response(path: Path, recent: int = 3) -> dict[str, Any]:
    """Build the wire payload returned by ``GET /api/feedback-inbox``.

    Shape matches ``web/src/store/feedback-inbox.ts`` `FeedbackInboxResponse`:
        { unfiledCount: int, bullets: [{ id, text, source, addedAt }, …] }
    """
    bullets = load_inbox(path)
    # Single total ordering for the "most recent" head:
    #   1. Dated bullets come first, ordered newest → oldest.
    #   2. Undated bullets follow, in the order they appear in the file.
    # The file-index tiebreaker keeps the order stable across polls so
    # bullet ids don't shuffle in the UI.
    indexed = list(enumerate(bullets))
    dated = sorted(
        (pair for pair in indexed if pair[1].added_at),
        key=lambda pair: (pair[1].added_at or "", -pair[0]),
        reverse=True,
    )
    undated = [pair for pair in indexed if not pair[1].added_at]
    ordered = [b for _, b in dated] + [b for _, b in undated]
    head = ordered[:recent]
    return {
        "unfiledCount": len(bullets),
        "bullets": [
            {
                "id": b.id,
                "text": b.text,
                "source": b.source,
                "addedAt": b.added_at,
            }
            for b in head
        ],
    }
