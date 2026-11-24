"""SQLite storage for user records (2.0.0).

One table, one row per user. The record is stored as JSON text, exactly as the
bot already uses it, so the rest of the code does not change. Banned users are
kept in their own column so the ban check at startup is one query.

The old per-user JSON files are not deleted. On the first start with an empty
database they are imported once, and they stay in place as a backup.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid     INTEGER PRIMARY KEY,
    data    TEXT    NOT NULL,
    banned  INTEGER NOT NULL DEFAULT 0,
    rev     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS users_banned ON users(banned);
"""


class SqliteBackend:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def read(self, uid: int) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT data FROM users WHERE uid = ?", (uid,)).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def write(self, uid: int, rec: dict) -> None:
        data = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            self._db.execute(
                "INSERT INTO users (uid, data, banned, rev) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(uid) DO UPDATE SET data = excluded.data, banned = excluded.banned, rev = excluded.rev",
                (uid, data, 1 if rec.get("banned") else 0, int(rec.get("rev") or 0)),
            )

    def ids(self) -> list[int]:
        with self._lock:
            return [int(r[0]) for r in self._db.execute("SELECT uid FROM users ORDER BY uid")]

    def banned_ids(self) -> set[int]:
        with self._lock:
            return {int(r[0]) for r in self._db.execute("SELECT uid FROM users WHERE banned = 1")}

    def close(self) -> None:
        with self._lock:
            self._db.close()


def import_json_dir(backend: SqliteBackend, json_dir: Path) -> int:
    """Copy every readable user JSON file into an empty database. Returns how many were imported.
    Unreadable files are skipped and left untouched."""
    imported = 0
    if not json_dir.exists():
        return 0
    for p in sorted(json_dir.glob("*.json")):
        if not p.stem.isdigit():
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            uid = int(p.stem)
            if not isinstance(rec, dict):
                raise ValueError("not a record")
        except (OSError, ValueError) as e:
            log.warning("Import skipped %s: %s", p.name, e)
            continue
        backend.write(uid, rec)
        imported += 1
    log.info("Imported %d user record(s) from JSON into the database.", imported)
    return imported
