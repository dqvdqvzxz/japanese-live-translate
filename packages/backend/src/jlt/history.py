"""Session history.

In memory by default. Meeting content is only written to disk when the user
explicitly opts in, and the UI says plainly that it is doing so
(docs/security.md §4).

The in-memory store is a bounded ring: a 60-minute meeting produces a few
hundred segments, but the bound is what makes an 8-hour session safe too.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from pathlib import Path

from .logging import get_logger
from .models import Segment

log = get_logger(__name__)

MAX_IN_MEMORY = 2000


class HistoryManager:
    def __init__(
        self,
        *,
        persist: bool = False,
        db_path: Path | None = None,
        max_items: int = MAX_IN_MEMORY,
    ) -> None:
        self._segments: deque[Segment] = deque(maxlen=max_items)
        self._persist = persist
        self._db_path = db_path
        self._connection: sqlite3.Connection | None = None
        if persist:
            self._open()

    def _open(self) -> None:
        if self._db_path is None:
            log.warning("persistence requested without a path; staying in memory")
            self._persist = False
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._db_path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS segments (
                session_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                source_text TEXT NOT NULL,
                target_text TEXT,
                created_at REAL NOT NULL,
                PRIMARY KEY (session_id, segment_id)
            )
            """
        )
        self._connection.commit()
        # Meeting content on disk is readable by anyone with the file, so at
        # minimum keep it out of other users' reach.
        self._db_path.chmod(0o600)

    def add(self, session_id: str, segment: Segment) -> None:
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

    def recent(self, limit: int = 200) -> list[Segment]:
        items = list(self._segments)[-limit:]
        return sorted(items, key=lambda s: s.seq)

    def clear(self) -> None:
        """Forget everything, including anything written to disk."""
        self._segments.clear()
        if self._connection is not None:
            self._connection.execute("DELETE FROM segments")
            self._connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def persisting(self) -> bool:
        return self._connection is not None

    def __len__(self) -> int:
        return len(self._segments)
