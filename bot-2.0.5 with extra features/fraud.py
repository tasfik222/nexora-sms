"""Simple abuse check (2.0.0).

If one account receives an unusually large number of codes in one hour, it is
flagged for review. Flagged accounts keep their balance, but withdrawals are on
hold until an admin clears the flag with /unflag <id>.
"""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Callable

MAX_CODES_PER_HOUR = int(os.getenv("FRAUD_MAX_CODES_PER_HOUR") or 60)
WINDOW_SECONDS = 3600


class CreditWatch:
    def __init__(self, limit: int = MAX_CODES_PER_HOUR, window: float = WINDOW_SECONDS,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self._now = now
        self._hits: dict[int, deque] = {}

    def note(self, uid: int) -> bool:
        """Record one code for this user. Returns True only at the moment the limit is passed."""
        t = self._now()
        hits = self._hits.setdefault(uid, deque())
        hits.append(t)
        while hits and t - hits[0] > self.window:
            hits.popleft()
        return len(hits) == self.limit + 1


def is_flagged(rec: dict) -> bool:
    return bool(rec.get("flagged"))


def flag(rec: dict, reason: str, when_iso: str) -> None:
    rec["flagged"] = True
    rec["flag_reason"] = reason[:200]
    rec["flagged_at"] = when_iso


def clear(rec: dict) -> None:
    for key in ("flagged", "flag_reason", "flagged_at"):
        rec.pop(key, None)
