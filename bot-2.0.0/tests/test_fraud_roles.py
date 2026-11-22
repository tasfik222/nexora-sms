import fraud


def test_note_flags_only_at_crossing():
    t = [0.0]
    w = fraud.CreditWatch(limit=3, window=3600, now=lambda: t[0])
    results = [w.note(1) for _ in range(5)]
    assert results == [False, False, False, True, False]


def test_window_expires_old_hits():
    t = [0.0]
    w = fraud.CreditWatch(limit=2, window=100, now=lambda: t[0])
    assert not w.note(7)
    assert not w.note(7)
    t[0] = 500.0
    assert not w.note(7)
    assert not w.note(7)


def test_flag_and_clear():
    rec = {"balance": 5}
    assert not fraud.is_flagged(rec)
    fraud.flag(rec, "test", "2026-01-01T00:00:00+00:00")
    assert fraud.is_flagged(rec)
    fraud.clear(rec)
    assert not fraud.is_flagged(rec)
    assert rec == {"balance": 5}


def test_staff_and_view_only_rules(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "ADMIN_IDS", {1})
    monkeypatch.setattr(bot, "VIEWER_IDS", {2})
    assert bot.is_staff(1) and bot.is_staff(2) and not bot.is_staff(3)
    assert not bot.is_admin(2)
    assert "home" in bot.VIEW_ONLY_ACTS and "ban" not in bot.VIEW_ONLY_ACTS and "payok" not in bot.VIEW_ONLY_ACTS
