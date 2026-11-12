"""
traffic.py - "Top Traffic": which ranges are receiving the most SMS right now.

A background task reads each site's live console (the same feed the channel uses) every few seconds and
keeps the rows of the last N minutes in memory. top(key) then counts them per range.

Environment variables (all optional):
    TRAFFIC_WINDOW_MIN   minutes of history to count (default 30)
    TRAFFIC_INTERVAL     seconds between console checks (default 8; sites cache the feed for ~5-10s)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from api_client import ApiError, Provider
from channel_feed import detect_service
from console_api import ConsoleEntry

log = logging.getLogger("voltx.traffic")

SEEN_LIMIT = 8000
MAX_ROWS = 20000          # safety cap per site


@dataclass
class TopRange:
    range: str            # e.g. "447384XXX"
    count: int
    service: str          # most common service in this range
    country: str = ""
    operator: str = ""


def range_key(number: str) -> str:
    """'447384XXX' stays as is; a fully visible number 447384512345 becomes 447384512XXX."""
    n = (number or "").strip().lstrip("+").upper()
    if not n or "X" in n or len(n) <= 6:
        return n
    return n[:-3] + "XXX"


def _prefix(r: str) -> str:
    return re.sub(r"[xX]+$", "", (r or "").strip()).lstrip("+")


async def resolve_listed_range(prov: Provider, traffic_range: str) -> str | None:
    """Find the range string the site itself lists for ordering that matches a console range."""
    want = _prefix(traffic_range)
    if not want:
        return None
    loose = None
    for svc in await prov.get_services():
        for r in svc.ranges:
            p = _prefix(r)
            if p == want:
                return r
            if loose is None and p and (p.startswith(want) or want.startswith(p)):
                loose = r
    return loose


class TrafficTracker:
    def __init__(self, fetch: Callable[[str], Awaitable[list[ConsoleEntry]]],
                 keys: Callable[[], list[str]]) -> None:
        self.fetch = fetch
        self.keys = keys
        self.window = max(60.0, float(os.getenv("TRAFFIC_WINDOW_MIN", "30") or 30) * 60)
        self.interval = max(5.0, float(os.getenv("TRAFFIC_INTERVAL", "8") or 8))
        self.rows: dict[str, deque] = {}                  # key -> deque[(ts, range, service, country, operator)]
        self.seen: dict[str, OrderedDict[str, None]] = {}
        self.ready: dict[str, bool] = {}                  # key -> got at least one successful poll
        self.task: asyncio.Task | None = None

    @property
    def window_min(self) -> int:
        return int(self.window // 60)

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()

    async def _run(self) -> None:
        while True:
            try:
                keys = self.keys()
                results = await asyncio.gather(*(self._safe_fetch(k) for k in keys))
                for key, entries in zip(keys, results):
                    if entries is not None:
                        self._ingest(key, entries)
                        self.ready[key] = True
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Traffic loop error (continuing)")
                await asyncio.sleep(self.interval)

    async def _safe_fetch(self, key: str) -> list[ConsoleEntry] | None:
        try:
            return await self.fetch(key)
        except ApiError as e:
            log.warning("Traffic: %s poll error: %s", key, e)
        except Exception:
            log.exception("Traffic: %s unexpected error", key)
        return None

    def _ingest(self, key: str, entries: list[ConsoleEntry]) -> None:
        rows = self.rows.setdefault(key, deque())
        seen = self.seen.setdefault(key, OrderedDict())
        now = time.time()
        for e in entries:
            if not e.entry_id or e.entry_id in seen:
                continue
            seen[e.entry_id] = None
            while len(seen) > SEEN_LIMIT:
                seen.popitem(last=False)
            rng = range_key(e.number)
            if not rng:
                continue
            ts = e.time / 1000 if e.time else now
            if ts > now + 300:                              # clock nonsense -> treat as "just now"
                ts = now
            service = e.service or detect_service(e.service, e.text)[1]
            rows.append((ts, rng, service, e.country, e.operator))
            if len(rows) > MAX_ROWS:
                rows.popleft()
        self._prune(key)

    def _prune(self, key: str) -> None:
        cutoff = time.time() - self.window
        rows = self.rows.get(key)
        if rows:
            fresh = deque(r for r in rows if r[0] >= cutoff)
            self.rows[key] = fresh

    def top(self, key: str, n: int = 10) -> list[TopRange]:
        self._prune(key)
        rows = self.rows.get(key) or ()
        per_range: dict[str, list] = {}
        for _, rng, service, country, operator in rows:
            per_range.setdefault(rng, []).append((service, country, operator))
        out = []
        for rng, items in per_range.items():
            def best(i: int) -> str:
                c = Counter(x[i] for x in items if x[i] and x[i] != "Unknown")
                return c.most_common(1)[0][0] if c else ""
            out.append(TopRange(range=rng, count=len(items), service=best(0),
                                country=best(1), operator=best(2)))
        out.sort(key=lambda t: t.count, reverse=True)
        return out[:n]
