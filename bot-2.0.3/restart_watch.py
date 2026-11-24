"""Restart reminder for the admins.

Free hosting stops the bot after about 4 days, so the bot is restarted by hand.
Every time the bot starts, the start time is saved in settings.json. When
RESTART_REMIND_HOURS (default 72) have passed since that start, every admin gets
one reminder to restart the bot. The reminder is sent once per start.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Iterable, Optional

log = logging.getLogger(__name__)

DHAKA = timezone(timedelta(hours=6))
REMIND_HOURS = float(os.getenv("RESTART_REMIND_HOURS") or 72)
CHECK_SECONDS = int(os.getenv("RESTART_CHECK_SECONDS") or 1800)


def parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def fmt_dhaka(dt: datetime) -> str:
    return dt.astimezone(DHAKA).strftime("%d %b %Y, %I:%M %p")


class RestartWatcher:
    def __init__(
        self,
        settings,
        admins: Iterable[int],
        send: Callable[[int, str], Awaitable[object]],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        remind_hours: float = REMIND_HOURS,
        check_seconds: int = CHECK_SECONDS,
    ) -> None:
        self.settings = settings
        self.admins = list(admins)
        self.send = send
        self._now = now
        self.remind_after = timedelta(hours=remind_hours)
        self.check_seconds = check_seconds
        self._task: Optional[asyncio.Task] = None

    def status_text(self) -> str:
        started = parse_iso(self.settings.started_at)
        if not started:
            return "Restart time is not recorded yet."
        due = started + self.remind_after
        left = due - self._now()
        line = f"Bot started: <b>{fmt_dhaka(started)}</b> (Dhaka time)\n"
        if self.settings.restart_reminded_for == self.settings.started_at:
            return line + "Reminder for this start has already been sent."
        if left.total_seconds() <= 0:
            return line + "Reminder is due now and will be sent at the next check."
        hours = left.total_seconds() / 3600
        return line + f"Reminder in about <b>{hours:.1f} h</b> ({fmt_dhaka(due)})."

    def reminder_text(self, started: datetime) -> str:
        return (
            "⏰ <b>Restart reminder</b>\n\n"
            f"Bot last started: <b>{fmt_dhaka(started)}</b> (Dhaka time).\n"
            "Free hosting stops the bot after about 4 days.\n\n"
            "Please restart the bot from the hosting panel now. "
            "The 3-day timer starts again from the new start time."
        )

    async def run_once(self) -> bool:
        """Send the reminder if it is due and not sent yet. Returns True when sent."""
        started = parse_iso(self.settings.started_at)
        if not started or self.settings.restart_reminded_for == self.settings.started_at:
            return False
        if self._now() - started < self.remind_after:
            return False
        sent = 0
        for aid in self.admins:
            try:
                await self.send(aid, self.reminder_text(started))
                sent += 1
            except Exception as e:  # one admin failing must not block the others
                log.warning("Restart reminder to %s failed: %s", aid, e)
        if sent:
            self.settings.set_restart_reminded(self.settings.started_at)
            log.info("Restart reminder sent to %d admin(s)", sent)
        return sent > 0

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                log.exception("Restart watcher check failed")
            await asyncio.sleep(self.check_seconds)

    async def start(self) -> None:
        if self._task is None and self.admins:
            self._task = asyncio.create_task(self._loop())
            log.info("Restart reminder on: %.0f h after start, checked every %d s",
                     self.remind_after.total_seconds() / 3600, self.check_seconds)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
