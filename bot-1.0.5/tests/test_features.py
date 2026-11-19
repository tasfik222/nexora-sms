import asyncio
import os
import tempfile

import features
from storage import UserStore


def test_parse_referrer():
    assert features.parse_referrer("ref123456789") == 123456789
    assert features.parse_referrer("ref12") is None
    assert features.parse_referrer("hello") is None
    assert features.parse_referrer("") is None


def test_referral_stats_default_zero():
    assert features.referral_stats({}) == (0, 0)


def test_attach_referrer_only_once_and_not_self():
    rec = {"user_id": 5}
    assert features.attach_referrer(rec, 9, 5) is True
    assert rec["referred_by"] == 9
    assert features.attach_referrer(rec, 7, 5) is False     # already linked
    assert features.attach_referrer({}, 5, 5) is False       # cannot refer yourself
    assert features.attach_referrer({"total_codes": 3}, 9, 5) is False  # old user


def test_split_reward_takes_share_from_user():
    assert features.split_reward({}, 1000) == (1000, 0)
    assert features.split_reward({"referred_by": 9}, 1000) == (800, 200)
    assert features.split_reward({"referred_by": 9}, 999) == (800, 199)


def test_referrer_is_credited_and_total_is_unchanged(monkeypatch):
    monkeypatch.setenv("LOCAL_DATA_DIR", tempfile.mkdtemp())
    store = UserStore()
    store.start() if not asyncio.iscoroutinefunction(store.start) else asyncio.run(store.start())
    asyncio.run(store.get(9, "boss"))
    rec_new = asyncio.run(store.get(5, "friend"))
    rec_new["referred_by"] = 9
    mine, share = features.split_reward(rec_new, 1000)
    assert asyncio.run(features.pay_referrer(store, rec_new, share, "voltx:a1")) == 200
    assert mine + share == 1000                  # nothing is added on top
    assert store.peek(9)["balance"] == 200
    assert store.peek(9)["ref_earned"] == 200


def test_same_sms_never_pays_referrer_twice(monkeypatch):
    monkeypatch.setenv("LOCAL_DATA_DIR", tempfile.mkdtemp())
    store = UserStore()
    store.start() if not asyncio.iscoroutinefunction(store.start) else asyncio.run(store.start())
    asyncio.run(store.get(9, "boss"))
    rec_new = asyncio.run(store.get(5, "friend"))
    rec_new["referred_by"] = 9
    asyncio.run(features.pay_referrer(store, rec_new, 200, "voltx:same"))
    assert asyncio.run(features.pay_referrer(store, rec_new, 200, "voltx:same")) == 0
    assert store.peek(9)["balance"] == 200


def test_no_referrer_no_payment(monkeypatch):
    monkeypatch.setenv("LOCAL_DATA_DIR", tempfile.mkdtemp())
    store = UserStore()
    store.start() if not asyncio.iscoroutinefunction(store.start) else asyncio.run(store.start())
    rec = asyncio.run(store.get(5, "alone"))
    assert asyncio.run(features.pay_referrer(store, rec, 200, "voltx:x")) == 0


def test_referrer_share_rounds_down():
    assert features.referrer_share(999) == 199
    assert features.referrer_share(0) == 0


def test_badges_by_codes_and_wins():
    rec = {"total_codes": 12, "arena": {"wins": 1}}
    names = features.badges(rec, "en")
    assert "First code" in names[0] and "10 codes" in names[1] and "First win" in names[2]
    assert features.badges({}, "en") == []
    assert "প্রথম কোড" in features.badges({"total_codes": 1}, "bn")[0]
