"""
storage.py - per-user data (balance + history), one JSON file per Telegram user.

Where the data lives
    ./data/users/<telegram_id>.json   (written immediately on every change)

Environment variables (all optional)
    LOCAL_DATA_DIR    default "data"
    LEADERBOARD_TIMEZONE   day boundary of the daily leaderboard (default OTP_TIMEZONE, else "Asia/Dhaka")

Daily counter (leaderboard): each record has "daily": {"date": "YYYY-MM-DD", "codes": N} = OTPs received
on that day. The day changes at 12:00 AM in LEADERBOARD_TIMEZONE (default OTP_TIMEZONE, else Asia/Dhaka), so the
leaderboard "resets" every night by itself.

Money: `balance` and history `amount` are whole PAISA (100 paisa = 1 taka); the files also carry
readable `balance_taka` / `amount_taka` fields. Old records are upgraded automatically at startup.

Safety rules built in
    * A user is only created fresh when no local file exists. A file that exists but is unreadable
      makes loading fail instead of silently resetting a balance to 0.
    * Every record has a `rev` counter.
    * One OTP id can only ever be credited once per user.
Assumes ONE running bot instance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from decimal import Decimal, InvalidOperation

log = logging.getLogger("voltx.store")

HISTORY_LIMIT = 50     # earnings entries kept per user
ID_LIMIT = 200         # credited OTP ids remembered per user (duplicate protection)


try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

_TZ = None


def _tz():
    """Timezone of the daily reset. Falls back to a fixed UTC+6 (Bangladesh has no DST) if tz data is missing."""
    global _TZ
    if _TZ is None:
        name = os.getenv("LEADERBOARD_TIMEZONE") or os.getenv("OTP_TIMEZONE") or "Asia/Dhaka"
        try:
            _TZ = ZoneInfo(name)
        except Exception:
            _TZ = timezone(timedelta(hours=6))
    return _TZ


def today_str() -> str:
    return datetime.now(_tz()).date().isoformat()


def seconds_to_reset() -> int:
    """Seconds until the next 12:00 AM (when the daily leaderboard starts again)."""
    now = datetime.now(_tz())
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((nxt - now).total_seconds()))


def _history_today(rec: dict, today: str) -> int:
    """OTPs credited today according to the (last 50) history entries - used when no daily counter exists yet."""
    n = 0
    for h in rec.get("history", []):
        try:
            if datetime.fromisoformat(h["time"]).astimezone(_tz()).date().isoformat() == today:
                n += 1
        except (KeyError, ValueError, TypeError):
            continue
    return n


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _taka(paisa: int) -> str:
    return f"{paisa // 100}.{paisa % 100:02d}"


class StorageError(Exception):
    """Could not safely load or save user data."""


def _to_paisa(value) -> int:
    """Whole numbers are already paisa. A decimal (0.3, "0.30") can only come from a hand-edited file and
    is read as TAKA, so 0.3 -> 30 paisa."""
    if isinstance(value, bool):
        raise StorageError(f"unreadable money value {value!r}")
    if isinstance(value, int):
        return value
    try:
        return int((Decimal(str(value)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        raise StorageError(f"unreadable money value {value!r}")


def _normalize(rec: dict) -> bool:
    """Repair money fields and add human-readable ones. Returns True if anything changed."""
    changed = False

    if type(rec.get("balance")) is not int:        # decimal / missing / text -> whole paisa
        rec["balance"] = _to_paisa(rec.get("balance", 0))
        changed = True
    for h in rec.get("history", []):
        if type(h.get("amount")) is not int:
            h["amount"] = _to_paisa(h.get("amount", 0))
            changed = True

    def put(d: dict, key: str, value) -> None:
        nonlocal changed
        if d.get(key) != value:
            d[key] = value
            changed = True

    put(rec, "unit", "paisa")                         # balance / amount are in paisa (100 paisa = 1 taka)
    put(rec, "balance_taka", _taka(rec.get("balance", 0)))
    for h in rec.get("history", []):
        put(h, "amount_taka", _taka(h.get("amount", 0)))
    return changed


class UserStore:
    def __init__(self, client=None) -> None:  # `client` kept only so old callers still work
        self.local_dir = Path(os.getenv("LOCAL_DATA_DIR") or "data") / "users"
        self._cache: dict[int, dict] = {}
        self._load_locks: dict[int, asyncio.Lock] = {}
        self.changes = 0     # bumped on every save; the backup task watches it

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.local_dir.mkdir(parents=True, exist_ok=True)
        log.info("User data is saved locally in %s", self.local_dir)

    async def migrate_all(self) -> None:
        """Open every known user once so old records get upgraded."""
        try:
            uids = {int(p.stem) for p in self.local_dir.glob("*.json") if p.stem.isdigit()}
            for uid in sorted(uids):
                try:
                    await self.get(uid)
                except StorageError as e:
                    log.warning("Migration: skipped user %s: %s", uid, e)
                except Exception:
                    log.exception("Migration: skipped user %s (unexpected error)", uid)
            log.info("Migration finished: %d user(s) checked.", len(uids))
        except Exception:
            log.exception("Migration failed")

    async def close(self) -> None:
        await self.flush()

    async def flush(self) -> None:
        """Nothing to push - every change is already written to disk. Kept for compatibility."""
        return None

    # ------------------------------------------------------------------ public API
    async def get(self, uid: int, username: str | None = None) -> dict:
        """Return the user's record, loading it (or creating a new one) if needed."""
        rec = self._cache.get(uid)
        if rec is None:
            async with self._load_locks.setdefault(uid, asyncio.Lock()):
                rec = self._cache.get(uid)
                if rec is None:
                    rec = self._read_local(uid)
                    if rec is None:
                        rec = self._new_record(uid, username)
                        self._cache[uid] = rec
                        self._save(uid)
                    else:
                        upgraded = _normalize(rec)  # may raise StorageError: a bad record is never cached
                        self._cache[uid] = rec
                        if upgraded:
                            self._save(uid)  # old-format / hand-edited record was repaired
        if username and rec.get("username") != username:
            rec["username"] = username
            self._save(uid)
        return rec

    def credit(self, uid: int, amount: int, number: str, code: Optional[str], otp_id: str) -> Optional[int]:
        """Add `amount` to the balance. Returns the new balance, or None if this OTP was already credited."""
        rec = self._cache.get(uid)
        if rec is None:
            log.error("credit() called for user %s that is not loaded", uid)
            return None
        if otp_id and otp_id in rec["credited_otp_ids"]:
            return None
        rec["balance"] += amount
        rec["total_codes"] += 1
        today = today_str()
        day = rec.get("daily")
        if not isinstance(day, dict) or day.get("date") != today:     # a new day (or first time): start today's count
            rec["daily"] = day = {"date": today, "codes": _history_today(rec, today)}
        day["codes"] = int(day.get("codes", 0)) + 1
        rec["history"].append({"time": _now(), "number": number, "code": code, "amount": amount, "otp_id": otp_id})
        rec["history"] = rec["history"][-HISTORY_LIMIT:]
        if otp_id:
            rec["credited_otp_ids"] = (rec["credited_otp_ids"] + [otp_id])[-ID_LIMIT:]
        self._save(uid)
        return rec["balance"]

    def debit(self, uid: int, amount: int) -> Optional[int]:
        """Take `amount` paisa from the balance (withdrawal hold). Returns the new balance, or None if the
        user is not loaded / the balance is too small."""
        rec = self._cache.get(uid)
        if rec is None or amount <= 0 or rec["balance"] < amount:
            return None
        rec["balance"] -= amount
        self._save(uid)
        return rec["balance"]

    def refund(self, uid: int, amount: int) -> Optional[int]:
        """Give `amount` paisa back (rejected / cancelled withdrawal). None if the user is not loaded."""
        rec = self._cache.get(uid)
        if rec is None or amount <= 0:
            return None
        rec["balance"] += amount
        self._save(uid)
        return rec["balance"]

    def peek(self, uid: int) -> Optional[dict]:
        """The user's record if it is already known (cache or local file); never creates one."""
        rec = self._cache.get(uid)
        if rec is not None:
            return rec
        try:
            return self._read_local(uid)
        except StorageError:
            return None

    def known_ids(self) -> list[int]:
        ids = {int(p.stem) for p in self.local_dir.glob("*.json") if p.stem.isdigit()}
        return sorted(ids | set(self._cache))

    def export_all(self) -> dict[int, dict]:
        out = {}
        for uid in self.known_ids():
            rec = self.peek(uid)
            if rec:
                out[uid] = rec
        return out

    async def set_name(self, uid: int, name: str | None) -> None:
        """Remember the user's Telegram first name (shown on the leaderboard). Never creates a new user."""
        name = (name or "").strip()[:64]
        if not name:
            return
        if uid not in self._cache:
            try:
                if self._read_local(uid) is None:
                    return
            except StorageError:
                return
        rec = await self.get(uid)
        if rec.get("first_name") != name:
            rec["first_name"] = name
            self._save(uid)

    def ranking(self, period: str = "day") -> list[dict]:
        """Leaderboard rows, most OTPs first (ties: who joined first).
        period "day" = OTPs received today (resets at 12:00 AM), "all" = lifetime total."""
        today = today_str()
        rows = []
        for uid, rec in self.export_all().items():
            if period == "all":
                codes = int(rec.get("total_codes", 0) or 0)
            else:
                day = rec.get("daily")
                codes = int(day.get("codes", 0)) if isinstance(day, dict) and day.get("date") == today \
                    else _history_today(rec, today)
            if codes > 0:
                name = (rec.get("first_name") or "").strip() or (f"@{rec['username']}" if rec.get("username") else "")
                rows.append({"uid": uid, "codes": codes, "name": name, "joined": rec.get("created_at", "")})
        rows.sort(key=lambda r: (-r["codes"], r["joined"], r["uid"]))
        return rows

    def stats(self) -> dict:
        recs = self.export_all().values()
        return {"users": len(recs), "balance": sum(int(r.get("balance", 0)) for r in recs),
                "codes": sum(int(r.get("total_codes", 0)) for r in recs)}

    def import_record(self, rec: dict) -> bool:
        """Merge one record from a backup: the record with the higher `rev` wins. True if it was stored."""
        try:
            uid = int(rec["user_id"])
            rec["rev"] = int(rec.get("rev", 0))
            rec.setdefault("username", "")
            rec.setdefault("total_codes", 0)
            rec.setdefault("history", [])
            rec.setdefault("credited_otp_ids", [])
            if not isinstance(rec["history"], list) or not isinstance(rec["credited_otp_ids"], list):
                return False
            _normalize(rec)
        except (KeyError, TypeError, ValueError, StorageError):
            return False
        cur = self.peek(uid)
        if cur is not None and int(cur.get("rev", 0)) >= rec["rev"]:
            return False
        self._cache[uid] = rec
        self._write_local(uid, rec)
        self.changes += 1
        return True

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _new_record(uid: int, username: str | None) -> dict:
        return {
            "user_id": uid, "username": username or "", "balance": 0, "total_codes": 0,
            "created_at": _now(), "updated_at": _now(), "rev": 0,
            "history": [], "credited_otp_ids": [],
        }

    def _save(self, uid: int) -> None:
        rec = self._cache[uid]
        rec["rev"] += 1
        rec["updated_at"] = _now()
        _normalize(rec)  # readable taka fields for humans (the bot ignores them)
        self.changes += 1
        self._write_local(uid, rec)

    def _local_path(self, uid: int) -> Path:
        return self.local_dir / f"{uid}.json"

    def _write_local(self, uid: int, rec: dict) -> None:
        try:
            self.local_dir.mkdir(parents=True, exist_ok=True)
            path = self._local_path(uid)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            log.error("Could not write local data for user %s: %s", uid, e)

    def _read_local(self, uid: int) -> Optional[dict]:
        path = self._local_path(uid)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise StorageError(f"user {uid}: local file unreadable ({e}); refusing to create a blank record")
