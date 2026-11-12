"""
backup.py - keeps the JSON data safe even if the bot / server is lost.

How it works (no GitHub, no extra service)
    * Every few minutes - and immediately after every withdrawal request / decision - the bot zips ALL data
      (users/*.json + withdrawals.json) and sends the zip to a private Telegram chat.
    * If BACKUP_CHAT_ID is set (a private channel/group where the bot is admin with "Post" + "Pin messages"),
      the newest backup is PINNED there. When the bot starts on a new/empty server it reads that pinned
      backup and restores everything by itself. Older backups are unpinned and deleted.
    * If BACKUP_CHAT_ID is not set, the zip is sent to every admin in a private chat instead. Restore is
      then manual: the admin sends the zip file back to the bot.
    * Restoring MERGES: for a user the record with the higher `rev` wins, for a withdrawal the later
      `updated_at` wins. Nothing is ever deleted.
    * Safety: a backup that has FEWER users/requests than the last good one (e.g. a blank server) is never
      uploaded automatically, so a good backup can't be overwritten by an empty one.

Environment variables (all optional)
    BACKUP_CHAT_ID         private channel/group id (-100...), bot must be admin there
    BACKUP_INTERVAL_MIN    max minutes between automatic backups when something changed (default 10)
    LOCAL_DATA_DIR         default "data"  (local copies of the last 10 backups go to <dir>/backups/)
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from telegram.error import TelegramError

from storage import UserStore, _now
from withdrawals import WithdrawStore

log = logging.getLogger("voltx.backup")

TAG = "VOLTX-BACKUP"
MAX_UNPACKED = 50 * 1024 * 1024
KEEP_LOCAL = 10
_USER_FILE = re.compile(r"^users/(\d+)\.json$")


class BackupError(Exception):
    pass


def build_backup(users: UserStore, wd: WithdrawStore) -> tuple[bytes, dict]:
    recs = users.export_all()
    payload = wd.export()
    meta = {"format": 1, "created_at": _now(), "users": len(recs), "requests": len(payload["requests"])}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps(meta, indent=2))
        for uid, rec in sorted(recs.items()):
            z.writestr(f"users/{uid}.json", json.dumps(rec, ensure_ascii=False, indent=2))
        z.writestr("withdrawals.json", json.dumps(payload, ensure_ascii=False, indent=2))
    return buf.getvalue(), meta


def read_backup(data: bytes) -> tuple[dict[int, dict], list[dict], int, dict]:
    """Validate a backup zip and return (users, requests, next_id, meta). Only known file names are read."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BackupError("This is not a valid zip file.")
    users: dict[int, dict] = {}
    reqs: list[dict] = []
    next_id, meta, found = 1, {}, False
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
            raise BackupError("This backup is too large.")
        for info in z.infolist():
            name = info.filename
            try:
                if name == "meta.json":
                    meta, found = json.loads(z.read(info)), True
                elif name == "withdrawals.json":
                    d = json.loads(z.read(info))
                    reqs = [r for r in d.get("requests", []) if isinstance(r, dict)]
                    next_id, found = int(d.get("next_id", 1)), True
                else:
                    m = _USER_FILE.match(name)
                    if m:
                        rec = json.loads(z.read(info))
                        if isinstance(rec, dict):
                            users[int(m.group(1))] = rec
                            found = True
            except (ValueError, TypeError, AttributeError):
                raise BackupError(f"The file {name} inside the zip is damaged.")
    if not found:
        raise BackupError("No bot data found in this zip.")
    return users, reqs, next_id, meta if isinstance(meta, dict) else {}


def restore(users: UserStore, wd: WithdrawStore, data: bytes) -> dict:
    u, reqs, next_id, meta = read_backup(data)
    users_done = 0
    for uid, rec in u.items():
        try:
            if int(rec.get("user_id", uid)) != uid:
                continue
        except (TypeError, ValueError):
            continue
        rec["user_id"] = uid
        users_done += users.import_record(rec)
    reqs_done = sum(1 for r in reqs if wd.merge_record(r))
    wd.bump_next_id(next_id)
    return {"users_in_backup": len(u), "users_restored": users_done,
            "requests_in_backup": len(reqs), "requests_restored": reqs_done,
            "created_at": str(meta.get("created_at", ""))}


class BackupManager:
    def __init__(self, bot, users: UserStore, wd: WithdrawStore, admin_ids: list[int]) -> None:
        self.bot, self.users, self.wd, self.admin_ids = bot, users, wd, list(admin_ids)
        raw = (os.getenv("BACKUP_CHAT_ID") or "").strip()
        self.chat: int | str | None = (int(raw) if raw.lstrip("-").isdigit() else raw) or None
        self.interval = max(60.0, float(os.getenv("BACKUP_INTERVAL_MIN", "10") or 10) * 60)
        self.dir = Path(os.getenv("LOCAL_DATA_DIR") or "data") / "backups"
        self.ready = not self.chat          # with a backup chat we first read its pinned backup
        self.baseline = (0, 0)              # (users, requests) of the last good backup
        self.last_sig: tuple | None = None
        self.last_sent = float("-inf")
        self.last_ok_at = ""
        self.last_msg_id: int | None = None
        self.pin_ok: bool | None = None
        self.startup_note = ""
        self.urgent = False
        self._warned = False
        self._lock = asyncio.Lock()
        self.task: asyncio.Task | None = None

    # ------------------------------------------------------------------ public
    @property
    def mode(self) -> str:
        return "backup chat (pinned)" if self.chat else ("admin DMs" if self.admin_ids else "OFF (local files only)")

    def status_line(self) -> str:
        if not self.chat and not self.admin_ids:
            return "💾 Backup: OFF - set ADMIN_IDS / BACKUP_CHAT_ID"
        last = (self.last_ok_at[:16].replace("T", " ") + " UTC") if self.last_ok_at else "not yet"
        pin = "" if self.pin_ok is None or not self.chat else (" · pinned ✅" if self.pin_ok else " · ⚠️ cannot pin")
        wait = "" if self.ready else " · ⏳ waiting for the backup chat"
        return f"💾 Backup → {self.mode} · last: {last}{pin}{wait}"

    def request_soon(self) -> None:
        self.urgent = True

    async def start(self) -> None:
        await self._try_startup()
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()

    # ------------------------------------------------------------------ startup: read the pinned backup
    async def _try_startup(self) -> None:
        if self.ready or not self.chat:
            return
        try:
            chat = await self.bot.get_chat(self.chat)
        except TelegramError as e:
            log.error("Cannot read BACKUP_CHAT_ID=%s: %s (backups are paused until it works)", self.chat, e)
            self.startup_note = f"cannot reach the backup chat: {e}"
            return
        pm = getattr(chat, "pinned_message", None)
        if not pm or not getattr(pm, "document", None):
            log.info("Backup chat has no pinned backup yet - starting fresh.")
            self.startup_note = "no pinned backup yet"
            self.ready = True
            return
        self.last_msg_id = pm.message_id
        m = re.search(rf"{TAG} users=(\d+) requests=(\d+)", pm.caption or "")
        if m:
            self.baseline = (int(m.group(1)), int(m.group(2)))
        try:
            f = await self.bot.get_file(pm.document.file_id)
            data = bytes(await f.download_as_bytearray())
            res = restore(self.users, self.wd, data)
        except (TelegramError, BackupError) as e:
            log.error("Could not restore the pinned backup: %s (will retry)", e)
            self.startup_note = f"pinned backup could not be read: {e}"
            return
        self.ready = True
        self.startup_note = (f"restored from the pinned backup: {res['users_restored']} user(s), "
                             f"{res['requests_restored']} request(s) added/updated")
        log.info("Backup startup: %s", self.startup_note)
        if res["users_restored"] or res["requests_restored"]:
            await self._tell_admins(f"♻️ <b>Data restored automatically</b>\nFrom the pinned backup "
                                    f"({res['created_at'][:16].replace('T', ' ')} UTC): "
                                    f"{res['users_restored']} user(s), {res['requests_restored']} request(s).")

    # ------------------------------------------------------------------ background loop
    def _signature(self) -> tuple:
        return (self.users.changes, self.wd.changes)

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
                if not self.ready:
                    await self._try_startup()
                    continue
                if not (self.chat or self.admin_ids) or self._signature() == self.last_sig:
                    continue
                age = time.monotonic() - self.last_sent
                if age >= self.interval or (self.urgent and age >= 20):
                    await self.send_backup()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Backup loop error (continuing)")

    # ------------------------------------------------------------------ sending
    async def send_backup(self, force: bool = False) -> tuple[bool, str]:
        async with self._lock:
            if not (self.chat or self.admin_ids):
                return False, "No backup destination: set ADMIN_IDS or BACKUP_CHAT_ID."
            if not self.ready:
                return False, "The backup chat could not be read yet, so nothing was sent (to protect the old backup)."
            sig = self._signature()
            data, meta = build_backup(self.users, self.wd)
            shrunk = meta["users"] < self.baseline[0] or meta["requests"] < self.baseline[1]
            if shrunk and not force:
                msg = (f"Backup blocked: this server has {meta['users']} users / {meta['requests']} requests but the "
                       f"last good backup had {self.baseline[0]} / {self.baseline[1]}. Restore the old backup first "
                       f"(send the zip here) or use Backup now to override.")
                log.error(msg)
                if not self._warned:
                    self._warned = True
                    await self._tell_admins("⚠️ " + msg)
                return False, msg
            self._save_local(data)
            name = f"voltx-backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip"
            caption = f"{TAG} users={meta['users']} requests={meta['requests']}\n{meta['created_at']}"
            try:
                if self.chat:
                    sent = await self.bot.send_document(self.chat, document=data, filename=name,
                                                        caption=caption, disable_notification=True)
                    await self._pin_and_clean(sent.message_id, delete_old=not shrunk)
                else:
                    for aid in self.admin_ids:
                        try:
                            await self.bot.send_document(aid, document=data, filename=name, caption=caption,
                                                         disable_notification=True)
                        except TelegramError as e:
                            log.warning("Could not send the backup to admin %s: %s", aid, e)
            except TelegramError as e:
                log.error("Backup send failed: %s", e)
                return False, f"Telegram refused the backup: {e}"
            self.last_sig, self.last_sent, self.urgent = sig, time.monotonic(), False
            self.baseline = (meta["users"], meta["requests"])
            self._warned = False
            self.last_ok_at = meta["created_at"]
            return True, f"Backup sent: {meta['users']} users, {meta['requests']} requests."

    async def _pin_and_clean(self, new_id: int, delete_old: bool) -> None:
        old = self.last_msg_id
        self.last_msg_id = new_id
        try:
            await self.bot.pin_chat_message(self.chat, new_id, disable_notification=True)
            self.pin_ok = True
        except TelegramError as e:
            self.pin_ok = False
            log.warning("Could not pin the backup (give the bot the 'Pin messages' right): %s", e)
            return
        if old and old != new_id:
            try:
                await self.bot.unpin_chat_message(self.chat, old)
            except TelegramError:
                pass
            if delete_old:
                try:
                    await self.bot.delete_message(self.chat, old)
                except TelegramError:
                    pass

    def _save_local(self, data: bytes) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / f"backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip").write_bytes(data)
            for old in sorted(self.dir.glob("backup-*.zip"))[:-KEEP_LOCAL]:
                old.unlink(missing_ok=True)
        except OSError as e:
            log.warning("Could not write a local backup copy: %s", e)

    async def _tell_admins(self, text: str) -> None:
        for aid in self.admin_ids:
            try:
                await self.bot.send_message(aid, text, parse_mode="HTML")
            except TelegramError as e:
                log.warning("Could not message admin %s: %s", aid, e)
