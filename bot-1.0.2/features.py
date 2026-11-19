"""Referral share and badges.

Pure rules here (no Telegram). bot.py calls these from /start, the OTP delivery
path and the /badges command.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

log = logging.getLogger(__name__)

REFERRAL_PERCENT = int(os.getenv("REFERRAL_PERCENT") or 20)   # share of each reward the referrer gets

_REF_RE = re.compile(r"^ref(\d{3,20})$")

# (min codes received, emoji, English name, Bangla name)
CODE_BADGES = [
    (1, "🥉", "First code", "প্রথম কোড"),
    (10, "🥈", "10 codes", "১০টি কোড"),
    (50, "🥇", "50 codes", "৫০টি কোড"),
    (100, "💎", "100 codes", "১০০টি কোড"),
]
ARENA_BADGES = [
    (1, "🎮", "First win", "প্রথম জয়"),
    (10, "🏅", "10 wins", "১০টি জয়"),
]

TEXT = {
    "en": {
        "invite": "👥 <b>Invite friends</b>\n\nShare this link. You get <b>{percent}%</b> of every reward your friends earn, every time they get a code.\n\n"
                  "👤 Friends joined: <b>{count}</b>\n💰 Earned from friends: <b>{earned}</b>\n\n<code>{link}</code>",
        "badges_title": "🏅 <b>Your badges</b>",
        "badges_none": "No badges yet. Get your first number to earn one!",
        "dash_title": "📊 <b>Dashboard</b>",
        "dash": "👥 Users: <b>{users}</b>\n📨 SMS codes: <b>{codes}</b>\n💰 Balances: <b>{balance}</b>\n"
                "⏳ Pending withdrawals: <b>{pending}</b>\n🔗 Referral payouts so far: <b>{refs}</b>",
    },
    "bn": {
        "invite": "👥 <b>বন্ধুদের আমন্ত্রণ করুন</b>\n\nএই লিংক শেয়ার করুন। বন্ধুরা প্রতিটি কোডে যা পাবেন, তার <b>{percent}%</b> আপনি পাবেন।\n\n"
                  "👤 যোগ দিয়েছেন: <b>{count}</b>\n💰 বন্ধুদের থেকে আয়: <b>{earned}</b>\n\n<code>{link}</code>",
        "badges_title": "🏅 <b>আপনার ব্যাজ</b>",
        "badges_none": "এখনো কোনো ব্যাজ নেই। প্রথম নম্বর নিলে ব্যাজ পাবেন!",
        "dash_title": "📊 <b>ড্যাশবোর্ড</b>",
        "dash": "👥 User: <b>{users}</b>\n📨 SMS কোড: <b>{codes}</b>\n💰 ব্যালেন্স: <b>{balance}</b>\n"
                "⏳ অপেক্ষারত উত্তোলন: <b>{pending}</b>\n🔗 রেফারেল থেকে মোট দেওয়া: <b>{refs}</b>",
    },
}


def tr(lang: str, key: str, **kw) -> str:
    table = TEXT.get(lang) or TEXT["en"]
    return table[key].format(**kw)


def parse_referrer(arg: str) -> Optional[int]:
    """Read the referrer id from a /start argument like 'ref123456789'."""
    m = _REF_RE.match((arg or "").strip())
    return int(m.group(1)) if m else None


def attach_referrer(rec: dict, referrer_id: int, own_id: int) -> bool:
    """Link a new user to a referrer once. Returns True if linked."""
    if referrer_id == own_id or rec.get("referred_by") or rec.get("total_codes"):
        return False
    rec["referred_by"] = int(referrer_id)
    return True


def referral_stats(rec: dict) -> tuple[int, int]:
    """(friends who joined through this user's link, total earned from them)."""
    return int(rec.get("ref_count") or 0), int(rec.get("ref_earned") or 0)


def referrer_share(price: int) -> int:
    """The referrer's part of one reward, in paisa (integer maths, rounded down)."""
    return max(0, int(price)) * REFERRAL_PERCENT // 100


def split_reward(rec: dict, price: int) -> tuple[int, int]:
    """(amount for the user, amount for their referrer). The referrer's share comes out of the user's reward."""
    if not rec.get("referred_by"):
        return int(price), 0
    share = referrer_share(price)
    return int(price) - share, share


async def pay_referrer(store, rec: dict, share: int, otp_key: str) -> int:
    """Credit the referrer with `share` paisa for one SMS. Returns the amount paid (0 if none)."""
    referrer = rec.get("referred_by")
    if not referrer or share <= 0:
        return 0
    try:
        ref_rec = await store.get(int(referrer))
        if store.credit(int(referrer), share, "referral", None, f"ref:{otp_key}") is None:
            return 0                              # already paid for this SMS
        ref_rec["ref_earned"] = int(ref_rec.get("ref_earned") or 0) + share
        store.save(int(referrer))
    except Exception:
        log.exception("Referral share failed for referrer %s", referrer)
        return 0
    return share


def count_referrals(store) -> tuple[int, int]:
    """(number of referred users, total paid to referrers) - used by the admin dashboard."""
    recs = store.export_all().values()
    return (sum(1 for r in recs if r.get("referred_by")),
            sum(int(r.get("ref_earned") or 0) for r in recs))


def badges(rec: dict, lang: str = "en") -> list[str]:
    """The badges a user has earned, as display strings."""
    out = []
    codes = int(rec.get("total_codes") or 0)
    for need, emoji, en, bn in CODE_BADGES:
        if codes >= need:
            out.append(f"{emoji} {bn if lang == 'bn' else en}")
    arena = rec.get("arena") or {}
    wins = int(arena.get("wins") or 0)
    for need, emoji, en, bn in ARENA_BADGES:
        if wins >= need:
            out.append(f"{emoji} {bn if lang == 'bn' else en}")
    return out
