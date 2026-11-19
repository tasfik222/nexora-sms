import asyncio
from datetime import datetime, timedelta, timezone

from restart_watch import RestartWatcher
from settings import SettingsStore


def make(tmp_path, monkeypatch, started, now):
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    st = SettingsStore()
    st.mark_started(started.isoformat(timespec="seconds"))
    sent = []

    async def send(uid, text):
        sent.append(uid)

    clock = {"now": now}
    w = RestartWatcher(st, [1, 2], send, now=lambda: clock["now"], remind_hours=72)
    return st, w, sent, clock


def test_no_reminder_before_three_days(tmp_path, monkeypatch):
    t0 = datetime(2026, 11, 19, tzinfo=timezone.utc)
    st, w, sent, clock = make(tmp_path, monkeypatch, t0, t0 + timedelta(hours=71))
    assert asyncio.run(w.run_once()) is False
    assert sent == []


def test_reminder_once_after_three_days(tmp_path, monkeypatch):
    t0 = datetime(2026, 11, 19, tzinfo=timezone.utc)
    st, w, sent, clock = make(tmp_path, monkeypatch, t0, t0 + timedelta(hours=73))
    assert asyncio.run(w.run_once()) is True
    assert sent == [1, 2]
    assert asyncio.run(w.run_once()) is False  # not again for the same start
    assert sent == [1, 2]


def test_restart_resets_the_timer(tmp_path, monkeypatch):
    t0 = datetime(2026, 11, 19, tzinfo=timezone.utc)
    st, w, sent, clock = make(tmp_path, monkeypatch, t0, t0 + timedelta(hours=73))
    assert asyncio.run(w.run_once()) is True
    # bot restarted at t0+74h: new start time, so a new reminder is due only after 72 more hours
    st.mark_started((t0 + timedelta(hours=74)).isoformat(timespec="seconds"))
    clock["now"] = t0 + timedelta(hours=100)
    assert asyncio.run(w.run_once()) is False
    clock["now"] = t0 + timedelta(hours=147)
    assert asyncio.run(w.run_once()) is True


def test_reminder_survives_settings_reload(tmp_path, monkeypatch):
    t0 = datetime(2026, 11, 19, tzinfo=timezone.utc)
    st, w, sent, clock = make(tmp_path, monkeypatch, t0, t0 + timedelta(hours=80))
    assert asyncio.run(w.run_once()) is True
    st2 = SettingsStore()  # simulates a restart reading settings.json
    assert st2.restart_reminded_for == st2.started_at
