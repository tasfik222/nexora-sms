"""Idle-reminder rules. Run from the project folder:  python -m pytest -q tests"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["LOCAL_DATA_DIR"] = tempfile.mkdtemp()

import nudge  # noqa: E402
from storage import UserStore  # noqa: E402


def ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, uid, text, kb):
        self.sent.append(uid)


async def make(users):
    store = UserStore()
    await store.start()
    for uid, seen in users.items():
        await store.get(uid, f"u{uid}")
        store._cache[uid]["last_seen"] = seen
    return store


def nudger(store, rec, banned=(), excluded=()):
    return nudge.Nudger(store, build=lambda: ("hi", None), is_banned=lambda u: u in banned,
                        is_excluded=lambda u: u in excluded, send=rec)


def open_hours(monkeypatch):
    monkeypatch.setattr(nudge, "START_HOUR", 0)
    monkeypatch.setattr(nudge, "END_HOUR", 24)


def test_idle_user_gets_one_reminder_per_idle_stretch(monkeypatch):
    open_hours(monkeypatch)

    async def run():
        store = await make({1: ago(4)})
        rec = Recorder()
        n = nudger(store, rec)
        await n.run_once()
        await n.run_once()                     # same idle stretch: no second message
        assert rec.sent == [1]
        # user comes back (new activity), then goes quiet again -> one more reminder is allowed
        store._cache[1]["last_seen"] = ago(0.5)
        await n.run_once()                     # active: nothing
        assert rec.sent == [1]
        store._cache[1]["last_seen"] = ago(4.25)
        await n.run_once()
        assert rec.sent == [1, 1]
    asyncio.run(run())


def test_active_user_gets_nothing(monkeypatch):
    open_hours(monkeypatch)

    async def run():
        store = await make({2: ago(1)})
        rec = Recorder()
        await nudger(store, rec).run_once()
        assert rec.sent == []
    asyncio.run(run())


def test_banned_admin_and_busy_users_are_skipped(monkeypatch):
    open_hours(monkeypatch)

    async def run():
        store = await make({3: ago(5), 4: ago(5), 5: ago(5)})
        rec = Recorder()
        await nudger(store, rec, banned={3}, excluded={4}).run_once()
        assert rec.sent == [5]
    asyncio.run(run())


def test_outside_day_hours_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(nudge, "START_HOUR", 0)
    monkeypatch.setattr(nudge, "END_HOUR", 0)     # empty window: never in the day

    async def run():
        store = await make({6: ago(10)})
        rec = Recorder()
        assert await nudger(store, rec).run_once() == 0
        assert rec.sent == []
    asyncio.run(run())


def test_user_without_last_seen_is_not_judged(monkeypatch):
    open_hours(monkeypatch)

    async def run():
        store = await make({7: ago(9)})
        store._cache[7].pop("last_seen", None)
        rec = Recorder()
        await nudger(store, rec).run_once()
        assert rec.sent == []
    asyncio.run(run())


def test_user_who_switched_reminders_off_gets_nothing(monkeypatch):
    open_hours(monkeypatch)

    async def run():
        store = await make({8: ago(6)})
        store._cache[8]["nudge_off"] = True
        rec = Recorder()
        await nudger(store, rec).run_once()
        assert rec.sent == []
        store._cache[8]["nudge_off"] = False     # switched back on -> eligible again
        await nudger(store, rec).run_once()
        assert rec.sent == [8]
    asyncio.run(run())
