"""
storage.py - per-user data (balance + history), one JSON file per Telegram user.

Where the data lives
    * Always: a local copy   ./data/users/<telegram_id>.json   (written immediately)
    * If configured: your GitHub repo  <GITHUB_DATA_DIR>/<telegram_id>.json
      (pushed in the background every few seconds, and once more on shutdown)

Environment variables (all optional; without GITHUB_* the bot keeps local files only)
    GITHUB_TOKEN      fine-grained token with "Contents: Read and write" on ONE repo
    GITHUB_REPO       "owner/name", e.g. "bornt/voltx-data"   (use a PRIVATE repo!)
    GITHUB_BRANCH     default "main"
    GITHUB_DATA_DIR   default "users"
    LOCAL_DATA_DIR    default "data"

Money: `balance` and history `amount` are whole PAISA (100 paisa = 1 taka); the files also carry
readable `balance_taka` / `amount_taka` fields. Old records are upgraded automatically at startup.

Safety rules built in
    * A user is only created fresh when we are SURE no record exists. If GitHub can't be
      reached and there is no local copy, loading fails instead of resetting a balance to 0.
    * Every record has a `rev` counter; when local and GitHub differ, the higher rev wins.
    * One OTP id can only ever be credited once per user.
Assumes ONE running bot instance (two instances would overwrite each other's commits).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from decimal import Decimal, InvalidOperation

log = logging.getLogger("voltx.store")

FLUSH_INTERVAL = 5     # seconds between background pushes to GitHub
HISTORY_LIMIT = 50     # earnings entries kept per user
ID_LIMIT = 200         # credited OTP ids remembered per user (duplicate protection)


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
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.token = (os.getenv("GITHUB_TOKEN") or "").strip()
        self.repo = (os.getenv("GITHUB_REPO") or "").strip()
        self.branch = (os.getenv("GITHUB_BRANCH") or "main").strip()
        self.remote_dir = (os.getenv("GITHUB_DATA_DIR") or "users").strip("/")
        self.local_dir = Path(os.getenv("LOCAL_DATA_DIR") or "data") / "users"

        self._http = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
        self._cache: dict[int, dict] = {}
        self._dirty: set[int] = set()
        self._shas: dict[int, str] = {}
        self._load_locks: dict[int, asyncio.Lock] = {}
        self._flush_lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    @property
    def github_enabled(self) -> bool:
        return bool(self.token and self.repo)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.local_dir.mkdir(parents=True, exist_ok=True)
        if self.github_enabled:
            log.info("User data: local copy + GitHub repo %s (branch %s, dir %s/)",
                     self.repo, self.branch, self.remote_dir)
            self._flush_task = asyncio.get_running_loop().create_task(self._flush_loop())
        else:
            log.warning("GITHUB_TOKEN / GITHUB_REPO not set - user data is saved LOCALLY only (%s).",
                        self.local_dir)

    async def migrate_all(self) -> None:
        """Open every known user (local files + GitHub folder) once so old records get upgraded and pushed."""
        try:
            uids = {int(p.stem) for p in self.local_dir.glob("*.json") if p.stem.isdigit()}
            if self.github_enabled:
                try:
                    uids |= set(await self._gh_list())
                except StorageError as e:
                    log.warning("Migration: could not list users on GitHub: %s", e)
            updated = 0
            for uid in sorted(uids):
                try:
                    await self.get(uid)
                except StorageError as e:
                    log.warning("Migration: skipped user %s: %s", uid, e)
                    continue
                except Exception:
                    log.exception("Migration: skipped user %s (unexpected error)", uid)
                    continue
                updated += uid in self._dirty
            log.info("Migration finished: %d user(s) checked, %d upgraded.", len(uids), updated)
        except Exception:
            log.exception("Migration failed")

    async def close(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
        await self.flush()
        await self._http.aclose()

    # ------------------------------------------------------------------ public API
    async def get(self, uid: int, username: str | None = None) -> dict:
        """Return the user's record, loading it (or creating a new one) if needed."""
        rec = self._cache.get(uid)
        if rec is None:
            async with self._load_locks.setdefault(uid, asyncio.Lock()):
                rec = self._cache.get(uid)
                if rec is None:
                    rec = await self._load(uid)
                    if rec is None:
                        rec = self._new_record(uid, username)
                        self._cache[uid] = rec
                        self._save(uid)
                    else:
                        upgraded = _normalize(rec)  # may raise StorageError: a bad record is never cached
                        self._cache[uid] = rec
                        self._write_local(uid, rec)  # keep a local safety copy of what GitHub gave us
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
        rec["history"].append({"time": _now(), "number": number, "code": code, "amount": amount, "otp_id": otp_id})
        rec["history"] = rec["history"][-HISTORY_LIMIT:]
        if otp_id:
            rec["credited_otp_ids"] = (rec["credited_otp_ids"] + [otp_id])[-ID_LIMIT:]
        self._save(uid)
        return rec["balance"]

    async def flush(self) -> None:
        """Push all changed users to GitHub (no-op without GitHub config)."""
        if not self.github_enabled:
            self._dirty.clear()
            return
        async with self._flush_lock:
            for uid in list(self._dirty):
                rec = self._cache.get(uid)
                if rec is None:
                    self._dirty.discard(uid)
                    continue
                rev = rec["rev"]
                try:
                    await self._gh_put(uid, rec)
                except StorageError as e:
                    log.warning("GitHub push for user %s failed (will retry): %s", uid, e)
                    continue
                except Exception:
                    log.exception("Unexpected error pushing user %s (will retry)", uid)
                    continue
                if rec["rev"] == rev:
                    self._dirty.discard(uid)

    # ------------------------------------------------------------------ internals
    async def _flush_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(FLUSH_INTERVAL)
                try:
                    await self.flush()
                except Exception:  # never let one bad push stop all future saves
                    log.exception("Unexpected error while pushing user data (will retry)")
        except asyncio.CancelledError:
            pass

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
        self._write_local(uid, rec)
        self._dirty.add(uid)

    # ---- local files
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
            log.error("Corrupt local data for user %s: %s", uid, e)
            return None

    # ---- loading (GitHub is the source of truth, local is the safety net)
    async def _load(self, uid: int) -> Optional[dict]:
        local = self._read_local(uid)
        remote, remote_ok = None, True
        if self.github_enabled:
            try:
                remote = await self._gh_get(uid)
            except StorageError as e:
                remote_ok = False
                log.warning("Could not read user %s from GitHub: %s", uid, e)

        candidates = [r for r in (remote, local) if r]
        if candidates:
            best = max(candidates, key=lambda r: r.get("rev", 0))
            if self.github_enabled and (remote is None or best.get("rev", 0) > remote.get("rev", 0)):
                self._dirty.add(uid)  # local is newer than GitHub: push it
            return best
        if self.github_enabled and not remote_ok:
            raise StorageError(f"user {uid}: GitHub unreachable and no local copy; refusing to create a blank record")
        return None

    # ---- GitHub contents API
    async def _gh_list(self) -> list[int]:
        url = f"https://api.github.com/repos/{self.repo}/contents/{self.remote_dir}"
        try:
            r = await self._http.get(url, params={"ref": self.branch}, headers=self._headers())
        except httpx.HTTPError as e:
            raise StorageError(f"network error: {e!r}")
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            raise StorageError(f"GitHub list returned {r.status_code}")
        names = [str(i.get("name", "")) for i in r.json() if isinstance(i, dict)]
        return [int(n[:-5]) for n in names if n.endswith(".json") and n[:-5].isdigit()]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, uid: int) -> str:
        return f"https://api.github.com/repos/{self.repo}/contents/{self.remote_dir}/{uid}.json"

    async def _gh_get(self, uid: int) -> Optional[dict]:
        try:
            r = await self._http.get(self._url(uid), params={"ref": self.branch}, headers=self._headers())
        except httpx.HTTPError as e:
            raise StorageError(f"network error: {e!r}")
        if r.status_code == 404:
            self._shas.pop(uid, None)
            return None
        if r.status_code in (401, 403):
            log.error("GitHub rejected the token / repo access (HTTP %s). Check GITHUB_TOKEN and GITHUB_REPO.", r.status_code)
            raise StorageError(f"GitHub auth error {r.status_code}")
        if r.status_code != 200:
            raise StorageError(f"GitHub GET returned {r.status_code}")
        data = r.json()
        try:
            self._shas[uid] = data["sha"]
            return json.loads(base64.b64decode(data["content"]).decode("utf-8"))
        except (KeyError, ValueError) as e:
            raise StorageError(f"unreadable record on GitHub: {e!r}")

    async def _gh_put(self, uid: int, rec: dict) -> None:
        content = base64.b64encode(json.dumps(rec, ensure_ascii=False, indent=2).encode("utf-8")).decode()
        for attempt in (1, 2):
            body = {"message": f"user {uid} rev {rec['rev']}", "content": content, "branch": self.branch}
            if self._shas.get(uid):
                body["sha"] = self._shas[uid]
            try:
                r = await self._http.put(self._url(uid), json=body, headers=self._headers())
            except httpx.HTTPError as e:
                raise StorageError(f"network error: {e!r}")
            if r.status_code in (200, 201):
                self._shas[uid] = r.json()["content"]["sha"]
                return
            if r.status_code in (409, 422) and attempt == 1:
                await self._gh_get(uid)  # refresh the file's sha, then retry once
                continue
            if r.status_code in (401, 403, 404):
                log.error("GitHub write refused (HTTP %s). Check token permissions, repo name and branch.", r.status_code)
            raise StorageError(f"GitHub PUT returned {r.status_code}")
