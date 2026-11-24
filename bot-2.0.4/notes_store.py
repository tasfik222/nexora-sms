"""Admin notes (2.0.0).

Each note is saved as its own row in the same SQLite file as the user data
(data/nexora.db), so notes stay after restarts and updates. A note is removed
only by /notedel with an explicit confirm.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_NOTE_CHARS = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id  INTEGER NOT NULL,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
"""


class NoteBook:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def add(self, author_id: int, text: str) -> int:
        """Save one note. Returns its number. Raises ValueError for empty text."""
        clean = (text or "").strip()
        if not clean:
            raise ValueError("empty note")
        clean = clean[:MAX_NOTE_CHARS]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO admin_notes (author_id, text, created_at) VALUES (?, ?, ?)",
                (int(author_id), clean, now),
            )
            return int(cur.lastrowid)

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM admin_notes").fetchone()[0])

    def page(self, offset: int, limit: int) -> list[dict]:
        """Newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, author_id, text, created_at FROM admin_notes ORDER BY id DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            ).fetchall()
        return [{"id": r[0], "author_id": r[1], "text": r[2], "created_at": r[3]} for r in rows]

    def delete(self, note_id: int) -> bool:
        """Remove one note by its number. Only called after the admin confirms."""
        with self._lock:
            cur = self._db.execute("DELETE FROM admin_notes WHERE id = ?", (int(note_id),))
            return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._db.close()
