"""Get Number 3: admin-added numbers + the Lamix panel for the SMS.

- Admin pastes numbers in the Telegram panel. Each number is used once.
- A user who orders gets a free number; it is marked used at once.
- OTPs come from the Lamix REST API `GET /api/v1/messages` (one request covers all active numbers).
- Token is read from the LAMIX_API_KEY environment variable only.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from api_client import ApiError, ApiKeyError, NoNumbersError, NumberOrder, Otp, Provider, Service

log = logging.getLogger("voltx.crapi")

LAMIX_URL = (os.getenv("LAMIX_URL") or "https://panel.lamix.org/api/v1/messages").strip()
WINDOW_MS = 20 * 60 * 1000          # same 20 minutes the bot watches an order for
MARGIN = timedelta(minutes=10)      # look a little earlier, in case the clocks differ
DHAKA = timezone(timedelta(hours=6))
NUM_RE = re.compile(r"^\d{8,15}$")
CACHE_SECONDS = 5.0                # gap between real requests (Lamix allows 40 per minute)
MAX_RECORDS = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS pool_numbers (
    number      TEXT PRIMARY KEY,
    status      TEXT NOT NULL,          -- 'free' or 'used'
    assigned_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS pool_status ON pool_numbers(status);
"""


def normalize_number(line: str) -> str:
    """Digits only. Spaces, dashes, brackets and + are removed; a leading 00 (international prefix) too."""
    digits = re.sub(r"[\s\-+()]", "", line or "")
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def parse_numbers(text: str) -> tuple[list[str], list[str]]:
    """Split pasted text (one number per line). Returns (normalized numbers, rejected lines)."""
    good, bad = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        digits = normalize_number(line)
        (good if NUM_RE.match(digits) else bad).append(digits if NUM_RE.match(digits) else line)
    return good, bad


class NumberPool:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def add(self, numbers: Iterable[str]) -> tuple[int, int]:
        """Add new free numbers. Returns (added, already_there)."""
        added = dup = 0
        with self._lock:
            for n in numbers:
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO pool_numbers (number, status, assigned_ms) VALUES (?, 'free', 0)", (n,))
                if cur.rowcount:
                    added += 1
                else:
                    dup += 1
        return added, dup

    def take_free(self, now_ms: int) -> str | None:
        """Give out one free number and mark it used. Returns None when none is free."""
        with self._lock:
            row = self._db.execute(
                "SELECT number FROM pool_numbers WHERE status = 'free' ORDER BY number LIMIT 1").fetchone()
            if not row:
                return None
            self._db.execute("UPDATE pool_numbers SET status = 'used', assigned_ms = ? WHERE number = ?",
                             (now_ms, row[0]))
            return row[0]

    def remove_free(self, numbers: Iterable[str]) -> tuple[int, int]:
        """Delete free numbers from the pool. Returns (removed, not_free_or_unknown). Used numbers are never removed."""
        removed = missed = 0
        with self._lock:
            for n in numbers:
                cur = self._db.execute("DELETE FROM pool_numbers WHERE number = ? AND status = 'free'", (n,))
                if cur.rowcount:
                    removed += 1
                else:
                    missed += 1
        return removed, missed

    def clear_free(self) -> int:
        """Delete every free number. Numbers already given out stay, so running orders are not broken."""
        with self._lock:
            cur = self._db.execute("DELETE FROM pool_numbers WHERE status = 'free'")
            return int(cur.rowcount or 0)

    def counts(self) -> tuple[int, int]:
        with self._lock:
            free = self._db.execute("SELECT COUNT(*) FROM pool_numbers WHERE status = 'free'").fetchone()[0]
            used = self._db.execute("SELECT COUNT(*) FROM pool_numbers WHERE status = 'used'").fetchone()[0]
        return int(free), int(used)

    def active(self, now_ms: int) -> dict[str, int]:
        """Numbers handed out in the last 20 minutes: {number: assigned_ms}."""
        with self._lock:
            rows = self._db.execute(
                "SELECT number, assigned_ms FROM pool_numbers WHERE status = 'used' AND assigned_ms >= ?",
                (now_ms - WINDOW_MS,)).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _iso_ms(text: str) -> int:
    """Lamix 'time' is ISO 8601 in UTC, e.g. 2026-08-27T09:14:03.117Z."""
    try:
        d = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def records_to_otps(data: Any, active: dict[str, int]) -> list[Otp]:
    """Turn a Lamix /messages reply into OTPs for the active numbers only. Old rows are skipped."""
    if not isinstance(data, dict):
        return []
    out: list[Otp] = []
    for row in data.get("records") or []:
        if not isinstance(row, dict):
            continue
        num = re.sub(r"\D", "", str(row.get("number") or ""))
        if num not in active:
            continue
        msg = str(row.get("content") or "")
        ms = _iso_ms(str(row.get("time") or ""))
        if ms and ms < active[num] - int(MARGIN.total_seconds() * 1000):
            continue                                   # SMS from before this number was given out
        otp_id = "lamix:" + hashlib.sha1(f"{row.get('time')}|{num}|{msg}".encode()).hexdigest()[:16]
        out.append(Otp(otp_id=otp_id, number=num, message=msg, time=ms))
    return out


class CRApiClient(Provider):
    """Lamix panel (Get Number 3). Kept under the key 'crapi' so the rest of the bot is unchanged."""
    key = "crapi"
    label = "Lamix"
    api_key_env = "LAMIX_API_KEY"

    def __init__(self, pool: NumberPool) -> None:
        super().__init__()
        self.pool = pool
        self._cache: list[Otp] | None = None
        self._last_sent = float("-inf")
        self._retry_at = 0.0

    async def get_services(self) -> list[Service]:
        return [Service(sid="pool", last_at=0, ranges=["pool"], labels=["Pool numbers"])]

    async def get_number(self, range_str: str) -> NumberOrder:
        self._require_key()
        n = self.pool.take_free(int(time.time() * 1000))
        if not n:
            raise NoNumbersError("No number is free right now. Please try again later.",
                                 log_message="pool empty")
        return NumberOrder(full_number="+" + n, no_plus_number=n, country="", operator="")

    async def get_otps(self) -> list[Otp]:
        self._require_key()
        now = time.monotonic()
        if self._cache is not None and now - self._last_sent < CACHE_SECONDS:
            return list(self._cache)
        if now < self._retry_at:                       # provider asked us to wait (Retry-After)
            return list(self._cache or [])
        return await self._fetch_otps()

    async def _fetch_otps(self) -> list[Otp]:
        active = self.pool.active(int(time.time() * 1000))
        if not active:
            return []
        start = min(active.values()) / 1000
        since = datetime.fromtimestamp(start, tz=timezone.utc) - MARGIN
        self._last_sent = time.monotonic()
        try:
            r = await self._http.get(
                LAMIX_URL,
                params={"from": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": MAX_RECORDS},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as e:
            raise ApiError("The SMS service is not reachable right now.", log_message=f"lamix: {e}")
        if r.status_code == 429:
            wait = _retry_after(r)
            self._retry_at = time.monotonic() + wait
            log.warning("lamix rate limited; waiting %d s as the provider asked", int(wait))
            return list(self._cache or [])
        try:
            data = r.json()
        except ValueError:
            snippet = " ".join(r.text[:120].split())
            raise ApiError("The SMS service sent an unexpected reply.",
                           log_message=f"lamix HTTP {r.status_code} not JSON: {snippet}")
        if r.status_code in (401,):
            raise ApiKeyError("This server has no valid API key. Please contact the bot admin.",
                              log_message=f"lamix HTTP 401: {str(data)[:120]}")
        if r.status_code != 200:
            code = data.get("error") if isinstance(data, dict) else None
            raise ApiError("The SMS service refused the request.",
                           log_message=f"lamix HTTP {r.status_code}: {code or str(data)[:120]}")
        otps = records_to_otps(data, active)
        self._cache = otps
        return list(otps)


def _retry_after(r: httpx.Response) -> float:
    """Seconds to wait after a 429: the Retry-After header, or retryAfterSeconds in the body."""
    try:
        return max(1.0, float(r.headers.get("Retry-After") or 0))
    except ValueError:
        pass
    try:
        return max(1.0, float((r.json() or {}).get("retryAfterSeconds") or 30))
    except (ValueError, TypeError, AttributeError):
        return 30.0
