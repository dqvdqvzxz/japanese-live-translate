"""Session history.

In memory by default; on disk only when the user opts in. Meeting content is
the most sensitive thing this application touches, so persistence stays an
explicit choice and the UI says plainly what it means (docs/security.md §4).

The consequence is visible in the product: with persistence off, the history
list holds only the session you are in. That is the honest trade, and the UI
offers to turn saving on rather than silently keeping nothing.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from collections import deque
from pathlib import Path

from .logging import get_logger
from .models import Segment, SessionRecord, SessionSummary, Usage

log = get_logger(__name__)

MAX_IN_MEMORY = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL DEFAULT '',
    file_path           TEXT,
    started_at          REAL NOT NULL,
    ended_at            REAL,
    duration_s          REAL NOT NULL DEFAULT 0,
    source_name         TEXT NOT NULL DEFAULT '',
    segments_translated INTEGER NOT NULL DEFAULT 0,
    segments_failed     INTEGER NOT NULL DEFAULT 0,
    audio_seconds_sent  REAL NOT NULL DEFAULT 0,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd  REAL NOT NULL DEFAULT 0,
    summary_json        TEXT
);
CREATE TABLE IF NOT EXISTS segments (
    session_id   TEXT NOT NULL,
    segment_id   TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    source_text  TEXT NOT NULL,
    target_text  TEXT,
    created_at   REAL NOT NULL,
    PRIMARY KEY (session_id, segment_id)
);
CREATE INDEX IF NOT EXISTS segments_by_session ON segments (session_id, seq);
CREATE INDEX IF NOT EXISTS sessions_by_date ON sessions (started_at DESC);
"""


class HistoryManager:
    def __init__(
        self,
        *,
        persist: bool = False,
        db_path: Path | None = None,
        transcript_dir: Path | None = None,
        max_items: int = MAX_IN_MEMORY,
    ) -> None:
        self._segments: deque[Segment] = deque(maxlen=max_items)
        self._persist = persist
        self._db_path = db_path
        #: Where readable transcript files are written, if the user chose one.
        self._transcript_dir = transcript_dir
        self._connection: sqlite3.Connection | None = None
        self._current: SessionRecord | None = None
        if persist:
            self._open()

    def _open(self) -> None:
        if self._db_path is None:
            log.warning("persistence requested without a path; staying in memory")
            self._persist = False
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA)
        self._migrate()
        self._connection.commit()
        # Meeting content on disk should at least be out of other users' reach.
        self._db_path.chmod(0o600)

    def reconfigure(
        self, *, persist: bool, db_path: Path | None, transcript_dir: Path | None
    ) -> None:
        """Apply a settings change without restarting.

        Picking a folder in Settings has to take effect at once; making the
        user quit and relaunch before anything is written would look like the
        setting had simply not worked.
        """
        self._transcript_dir = transcript_dir
        if persist == self.persisting and db_path == self._db_path:
            return
        self.close()
        self._persist = persist
        self._db_path = db_path
        if persist:
            self._open()

    def _migrate(self) -> None:
        """Add columns that later versions introduced.

        `CREATE TABLE IF NOT EXISTS` does nothing to a table that already
        exists, so a database written by an earlier build would be missing
        `name` and every query against it would fail.
        """
        assert self._connection is not None
        columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(sessions)")}
        if "name" not in columns:
            self._connection.execute(
                "ALTER TABLE sessions ADD COLUMN name TEXT NOT NULL DEFAULT ''"
            )
        if "file_path" not in columns:
            self._connection.execute("ALTER TABLE sessions ADD COLUMN file_path TEXT")

    @property
    def persisting(self) -> bool:
        return self._connection is not None

    # -- session lifecycle --------------------------------------------------

    def begin_session(self, session_id: str, source_name: str, started_at: float) -> None:
        self._segments.clear()
        self._current = SessionRecord(
            session_id=session_id, started_at=started_at, source_name=source_name
        )
        if self._connection is None:
            return
        self._connection.execute(
            "INSERT OR REPLACE INTO sessions (session_id, started_at, source_name) "
            "VALUES (?, ?, ?)",
            (session_id, started_at, source_name),
        )
        self._connection.commit()

    def rename(self, session_id: str, name: str) -> SessionRecord | None:
        """Set the user's name for a recording. An empty name clears it."""
        cleaned = " ".join(name.split())[:120]
        if self._current and self._current.session_id == session_id:
            self._current.name = cleaned
        if self._connection is not None:
            self._connection.execute(
                "UPDATE sessions SET name = ? WHERE session_id = ?", (cleaned, session_id)
            )
            self._connection.commit()
        return self.get_session(session_id)

    def write_transcript_file(self, session_id: str) -> str | None:
        """Write the readable transcript into the folder the user chose.

        Kept in step with the record rather than written once: a rename or a
        newly generated summary should be reflected on disk, and the old file
        removed, otherwise the folder fills with stale duplicates of the same
        meeting.

        Returns the path written, or None when no folder is configured.
        """
        if self._transcript_dir is None:
            return None
        record = self.get_session(session_id)
        if record is None:
            return None

        from .export import filename as export_filename
        from .export import render

        segments = self.session_segments(session_id)
        target = self._transcript_dir / export_filename(record, "md")
        previous = self._file_path(session_id)

        try:
            self._transcript_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(render(record, segments, "md"), encoding="utf-8")
            target.chmod(0o600)
        except OSError as exc:
            # A missing or read-only folder must not take the session down.
            log.warning("could not write the transcript file", extra={"error": type(exc).__name__})
            return None

        if previous and previous != str(target):
            with contextlib.suppress(OSError):
                Path(previous).unlink()

        if self._connection is not None:
            self._connection.execute(
                "UPDATE sessions SET file_path = ? WHERE session_id = ?",
                (str(target), session_id),
            )
            self._connection.commit()
        return str(target)

    def _file_path(self, session_id: str) -> str | None:
        if self._connection is None:
            return None
        row = self._connection.execute(
            "SELECT file_path FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row["file_path"] if row else None

    def end_session(self, record: SessionRecord) -> None:
        self._current = record
        if self._connection is None:
            return
        self._connection.execute(
            """
            UPDATE sessions SET ended_at = ?, duration_s = ?, segments_translated = ?,
                   segments_failed = ?, audio_seconds_sent = ?, input_tokens = ?,
                   output_tokens = ?, estimated_cost_usd = ?
            WHERE session_id = ?
            """,
            (
                record.ended_at or time.time(),
                record.duration_s,
                record.segments_translated,
                record.segments_failed,
                record.audio_seconds_sent,
                record.usage.input_tokens,
                record.usage.output_tokens,
                record.estimated_cost_usd,
                record.session_id,
            ),
        )
        self._connection.commit()

    # -- segments -----------------------------------------------------------

    def add(self, session_id: str, segment: Segment) -> None:
        # Replace rather than append: a segment is recorded when it is queued
        # and again when its translation lands, and the second must update the
        # first rather than duplicate the line.
        for index, existing in enumerate(self._segments):
            if existing.segment_id == segment.segment_id:
                self._segments[index] = segment
                break
        else:
            self._segments.append(segment)
        if self._connection is None:
            return
        self._connection.execute(
            "INSERT OR REPLACE INTO segments VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                segment.segment_id,
                segment.seq,
                segment.source_text,
                segment.target_text,
                segment.created_at,
            ),
        )
        self._connection.commit()

    def update(self, session_id: str, segment: Segment) -> None:
        """Record a translation that arrived after the segment was stored."""
        self.add(session_id, segment)

    def recent(self, limit: int = 200) -> list[Segment]:
        items = list(self._segments)[-limit:]
        return sorted(items, key=lambda s: s.seq)

    # -- history ------------------------------------------------------------

    def list_sessions(self, limit: int = 100) -> list[SessionRecord]:
        """Finished sessions, newest first.

        Without persistence this is just the session in progress, which is the
        visible cost of not saving meeting content by default.
        """
        if self._connection is None:
            return [self._current] if self._current else []
        rows = self._connection.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_record_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> SessionRecord | None:
        if self._connection is None:
            if self._current and self._current.session_id == session_id:
                return self._current
            return None
        row = self._connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return _record_from_row(row) if row else None

    def session_segments(self, session_id: str) -> list[Segment]:
        if self._connection is None:
            if self._current and self._current.session_id == session_id:
                return self.recent(limit=MAX_IN_MEMORY)
            return []
        rows = self._connection.execute(
            "SELECT * FROM segments WHERE session_id = ? ORDER BY seq", (session_id,)
        ).fetchall()
        return [
            Segment(
                segment_id=row["segment_id"],
                seq=row["seq"],
                source_text=row["source_text"],
                target_text=row["target_text"],
                created_at=row["created_at"],
                state="translated" if row["target_text"] else "failed",
            )
            for row in rows
        ]

    def save_summary(self, session_id: str, summary: SessionSummary) -> None:
        if self._current and self._current.session_id == session_id:
            self._current.summary = summary
        if self._connection is None:
            return
        self._connection.execute(
            "UPDATE sessions SET summary_json = ? WHERE session_id = ?",
            (summary.model_dump_json(), session_id),
        )
        self._connection.commit()

    def delete_session(self, session_id: str) -> None:
        if self._current and self._current.session_id == session_id:
            self._current = None
            self._segments.clear()
        if self._connection is None:
            return
        self._connection.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
        self._connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self._connection.commit()

    def clear(self) -> None:
        """Forget everything, including anything written to disk."""
        self._segments.clear()
        self._current = None
        if self._connection is not None:
            self._connection.execute("DELETE FROM segments")
            self._connection.execute("DELETE FROM sessions")
            self._connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __len__(self) -> int:
        return len(self._segments)


def _record_from_row(row: sqlite3.Row) -> SessionRecord:
    summary = None
    if row["summary_json"]:
        try:
            summary = SessionSummary.model_validate(json.loads(row["summary_json"]))
        except (json.JSONDecodeError, ValueError):
            log.warning("stored summary is unreadable; ignoring it")
    return SessionRecord(
        session_id=row["session_id"],
        name=row["name"] or "",
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        duration_s=row["duration_s"],
        source_name=row["source_name"],
        segments_translated=row["segments_translated"],
        segments_failed=row["segments_failed"],
        audio_seconds_sent=row["audio_seconds_sent"],
        usage=Usage(input_tokens=row["input_tokens"], output_tokens=row["output_tokens"]),
        estimated_cost_usd=row["estimated_cost_usd"],
        summary=summary,
        file_path=row["file_path"],
    )
