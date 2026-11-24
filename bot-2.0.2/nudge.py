"""
nudge.py - a gentle reminder for users who have been away for a while during the day.

Rule (per user):
  * only between NUDGE_START_HOUR and NUDGE_END_HOUR (Bangladesh time)
  * only after NUDGE_IDLE_HOURS with no activity (no message, no button)
  * only ONCE per idle stretch: after they come back and go quiet again, they may get one more reminder
  * never to banned users, admins, or users who currently have a number waiting for an SMS

Settings (optional env vars, defaults in brackets):
  NUDGE_IDLE_HOURS [3]   NUDGE_START_HOUR [9]   NUDGE_END_HOUR [23]   NUDGE_CHECK_SECONDS [900]
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

from telegram.constants import ParseMode
from telegram.error import TelegramError

log = logging.getLogger("voltx.nudge")

DHAKA = timezone(timedelta(hours=6))          # Bangladesh has no daylight saving


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


IDLE_HOURS = _env_num("NUDGE_IDLE_HOURS", 3)
START_HOUR = int(_env_num("NUDGE_START_HOUR", 9))
END_HOUR = int(_env_num("NUDGE_END_HOUR", 23))
CHECK_SECONDS = int(_env_num("NUDGE_CHECK_SECONDS", 900))
MAX_PER_RUN = 50                               # never send a burst of messages in one go


class Nudger:
    def __init__(self, store, build: Callable[[], tuple], is_banned: Callable[[int], bool],
                 is_excluded: Callable[[int], bool], send: Callable) -> None:
        """
        store        - UserStore (export_all / save)
        build        - returns (text, keyboard) for the reminder, built from the live traffic data
        is_banned    - banned users never get a reminder
        is_excluded  - admins, and users who are busy with a number right now
        send         - async send(uid, text, keyboard) -> coroutine
        """
        self.store = store
        self.build = build
        self.is_banned = is_banned
        self.is_excluded = is_excluded
        self.send = send
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("nudge run failed")
            await asyncio.sleep(CHECK_SECONDS)

    async def run_once(self) -> int:
        """Send reminders to every user who is due one. Returns how many were sent."""
        local_hour = datetime.now(DHAKA).hour
        if not (START_HOUR <= local_hour < END_HOUR):
            return 0
        now = datetime.now(timezone.utc)
        text, keyboard = self.build()
        sent = 0
        for rec in list(self.store.export_all().values()):
            last_seen = rec.get("last_seen")
            if not last_seen:
                continue                              # no activity recorded yet - nothing to judge by
            uid = int(rec["user_id"])
            try:
                last = datetime.fromisoformat(last_seen)
            except ValueError:
                continue
            if now - last < timedelta(hours=IDLE_HOURS):
                continue                              # still active enough
            if rec.get("nudged_for") == last_seen:
                continue                              # already reminded during this idle stretch
            if rec.get("nudge_off"):
                continue                              # the user switched reminders off
            if self.is_banned(uid) or self.is_excluded(uid):
                continue
            rec["nudged_for"] = last_seen             # marked first, so a failed send is not retried every 15 min
            self.store.save(uid)
            try:
                await self.send(uid, text, keyboard)
                sent += 1
            except TelegramError as e:                # e.g. the user blocked the bot
                log.info("nudge to %s not delivered: %s", uid, e)
            if sent >= MAX_PER_RUN:
                break
            await asyncio.sleep(0.05)
        if sent:
            log.info("nudge: sent %d reminder(s)", sent)
        return sent
