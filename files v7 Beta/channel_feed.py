"""
channel_feed.py - live console feed: every new row that appears on either site's console is
posted to a Telegram channel/group as a colourful card.

    Get Number 1 (Voltx)  -> blue card   🟦
    Get Number 2 (Zenex)  -> green card  🟩

Environment variables:
    CHANNEL_ID        required. "-1001234567890" or "@yourchannel". The bot must be in the chat
                      (admin with "Post messages" for a channel).
    FEED_INTERVAL     optional, seconds between checks (default 1, minimum 0.5)
    FEED_PER_MIN      optional, max messages per minute sent to the chat (default 18). Telegram allows only about
                      20 per minute in a group; above that it blocks the bot for 30-60 s ("flood-wait").
                      When more SMS arrive than that, several cards are packed into ONE message instead of
                      waiting - nothing is lost and nothing is delayed.
    FEED_PAUSE_EVERY  optional, after this many seconds of non-stop sending... (default 60)
    FEED_PAUSE_SEC    ...the feed sleeps this many seconds (default 5), then continues. 0 = never pause.
    FEED_MASK         optional, "1" (default) also hides the middle digits of fully visible numbers.
                      Console numbers that already end in XXX are shown as they are.
    FEED_BOT_LINK     optional, e.g. "https://t.me/YourBot" -> adds an "Open Bot" button under each card.

On startup the rows already on the console are only remembered (not posted).
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from collections import OrderedDict, deque
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from api_client import ApiError, extract_code
from console_api import ConsoleEntry

log = logging.getLogger("voltx.feed")

SEEN_LIMIT = 5000         # remembered rows per site
QUEUE_LIMIT = 500         # max cards waiting to be sent (oldest dropped beyond this)
MAX_MESSAGE = 3900        # cards are packed into one message up to this many characters (Telegram max 4096)

THEMES = [
    {"bar": "🟦", "dot": "🔵"},
    {"bar": "🟩", "dot": "🟢"},
    {"bar": "🟪", "dot": "🟣"},
]

SERVICES = {  # keyword -> (emoji, display name)
    "whatsapp": ("💬", "WhatsApp"), "telegram": ("✈️", "Telegram"), "instagram": ("📸", "Instagram"),
    "facebook": ("📘", "Facebook"), "google": ("🔍", "Google"), "gmail": ("✉️", "Gmail"),
    "tiktok": ("🎵", "TikTok"), "twitter": ("🐦", "Twitter / X"), "snapchat": ("👻", "Snapchat"),
    "discord": ("🎮", "Discord"), "amazon": ("📦", "Amazon"), "uber": ("🚗", "Uber"),
    "paypal": ("💳", "PayPal"), "microsoft": ("🪟", "Microsoft"), "apple": ("🍎", "Apple"),
    "netflix": ("🎬", "Netflix"), "viber": ("📞", "Viber"), "imo": ("🟡", "IMO"),
    "wechat": ("💚", "WeChat"), "signal": ("🔒", "Signal"),
}


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def detect_service(*texts: str) -> tuple[str, str]:
    low = " ".join(t for t in texts if t).lower()
    for key, val in SERVICES.items():
        if key in low:
            return val
    return "📱", "Unknown"


def mask_number(no_plus: str) -> str:
    """8801712345678 -> +88017••••5678. Numbers already masked with X are returned unchanged."""
    n = no_plus.lstrip("+")
    if "x" in n.lower() or len(n) <= 8:
        return n if "x" in n.lower() else "+" + n
    return f"+{n[:5]}{'•' * (len(n) - 9)}{n[-4:]}"


def build_card(e: ConsoleEntry, source_label: str, theme: dict, mask: bool) -> str:
    emoji, guess = detect_service(e.service, e.text)
    service = e.service or guess
    code = extract_code(e.text)
    number = mask_number(e.number) if mask else e.number
    bar = theme["bar"] * 8
    when = time.strftime("%d %b %Y · %H:%M:%S", time.localtime(e.time / 1000 if e.time else time.time()))

    lines = [bar, f"{theme['dot']} <b>NEW SMS RECEIVED</b>", bar, "",
             f"🌐 <b>Source:</b> <b>{html.escape(source_label)}</b>",
             f"{emoji} <b>Service:</b> {html.escape(service)}"]
    if e.country:
        lines.append(f"🌍 <b>Country:</b> {html.escape(e.country)}")
    if e.operator:
        lines.append(f"📡 <b>Operator:</b> {html.escape(e.operator)}")
    lines.append(f"☎️ <b>Number:</b> <code>{html.escape(number)}</code>")
    if code:
        lines.append(f"🔑 <b>Code:</b> <code>{html.escape(code)}</code>")
    lines += ["", f"💌 <b>Message</b>\n<blockquote>{html.escape(e.text.strip())}</blockquote>",
              f"🕒 <i>{when}</i>"]
    return "\n".join(lines)


class ChannelFeed:
    def __init__(self, bot, fetch: Callable[[str], Awaitable[list[ConsoleEntry]]],
                 order: Callable[[], list[str]], position: Callable[[str], int]) -> None:
        self.bot = bot
        self.fetch = fetch                       # async fetch(key) -> list[ConsoleEntry]
        self.order = order                       # site keys that have a console
        self.position = position                 # site key -> 1, 2 (the "Get Number N" number)
        self.channel = (os.getenv("CHANNEL_ID") or "").strip()
        self.interval = max(0.5, _num("FEED_INTERVAL", 1.0))
        self.per_min = int(min(19, max(1, _num("FEED_PER_MIN", 18))))
        self.pause_every = max(5.0, _num("FEED_PAUSE_EVERY", 60.0))
        self.pause_sec = max(0.0, _num("FEED_PAUSE_SEC", 5.0))
        self._window_start = 0.0                  # start of the current non-stop sending period
        self._pause_until = 0.0
        self._last_send = 0.0
        self.window = 60.0                         # the rate limit is per_min messages per this many seconds
        self._sent_at: deque[float] = deque()      # times of the messages sent during the last window
        self.mask = (os.getenv("FEED_MASK", "1").strip() != "0")
        link = (os.getenv("FEED_BOT_LINK") or "").strip()
        self.markup = (InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Open Bot", url=link)]])
                       if link.startswith("http") else None)
        self.seen: dict[str, OrderedDict[str, None]] = {}
        self.task: asyncio.Task | None = None
        self.sender: asyncio.Task | None = None
        self.pending: deque = deque()               # (site key, entry) cards waiting to be sent
        self.wake = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return bool(self.channel)

    def label_and_theme(self, key: str) -> tuple[str, dict]:
        n = self.position(key)
        return f"Get Number {n}", THEMES[(n - 1) % len(THEMES)]

    async def start(self) -> None:
        try:
            chat = await self.bot.get_chat(self.channel)
            log.info("Channel feed -> %s (%s)", chat.title or chat.username, self.channel)
        except TelegramError as e:
            log.error("Cannot access CHANNEL_ID=%s: %s. Add the bot to the chat (admin for a channel).",
                      self.channel, e)
        self.task = asyncio.create_task(self._run())
        self.sender = asyncio.create_task(self._send_loop())

    async def stop(self) -> None:
        for t in (self.task, self.sender):
            if t:
                t.cancel()

    async def _safe_fetch(self, key: str) -> list[ConsoleEntry] | None:
        try:
            return await self.fetch(key)
        except ApiError as e:
            log.warning("Feed: %s poll error: %s", key, e)
        except Exception:
            log.exception("Feed: %s unexpected error", key)
        return None

    def _remember(self, key: str, entry_id: str) -> None:
        d = self.seen.setdefault(key, OrderedDict())
        d[entry_id] = None
        while len(d) > SEEN_LIMIT:
            d.popitem(last=False)

    async def _run(self) -> None:
        keys = self.order()
        for key in keys:                                   # 1) silent baseline
            self.seen.setdefault(key, OrderedDict())
            for e in await self._safe_fetch(key) or []:
                self._remember(key, e.entry_id)
        log.info("Feed baseline ready for %s, watching every %.0fs.", ", ".join(keys), self.interval)

        while True:                                        # 2) watch
            try:
                await asyncio.sleep(self.interval)
                keys = self.order()
                results = await asyncio.gather(*(self._safe_fetch(k) for k in keys))
                for key, entries in zip(keys, results):
                    if entries is None:
                        continue
                    seen = self.seen.setdefault(key, OrderedDict())
                    fresh = [e for e in entries if e.entry_id and e.entry_id not in seen]
                    fresh.sort(key=lambda e: e.time)       # oldest first
                    for e in fresh:
                        self._remember(key, e.entry_id)
                        if len(self.pending) >= QUEUE_LIMIT:
                            self.pending.popleft()               # drop the oldest waiting card
                            log.warning("Feed: send queue full, dropped the oldest card")
                        self.pending.append((key, e))            # polling never waits for Telegram
                        self.wake.set()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Feed loop error (continuing)")

    async def _breather(self) -> None:
        """After `pause_every` seconds of non-stop sending the feed sleeps `pause_sec` seconds. A card that
        arrives after a quiet moment is never delayed by this."""
        now = time.monotonic()
        if now - self._last_send > 2.0:                    # quiet before this: start a fresh sending period
            self._window_start = now
        elif self.pause_sec and now - self._window_start >= self.pause_every:
            self._window_start = now + self.pause_sec      # the next period begins after the pause
            self._pause_until = now + self.pause_sec
        wait = self._pause_until - now
        if wait > 0:
            await asyncio.sleep(wait)

    async def _rate_wait(self) -> None:
        """Keeps us under Telegram's per-chat limit: at most `per_min` messages in any 60 seconds. While we
        wait, new cards pile up in `pending` and go out together in one message."""
        while True:
            now = time.monotonic()
            while self._sent_at and now - self._sent_at[0] >= self.window:
                self._sent_at.popleft()
            wait = max(self._pause_until - now,
                       (self._sent_at[0] + self.window - now) if len(self._sent_at) >= self.per_min else 0)
            if wait <= 0:
                return
            await asyncio.sleep(min(wait, 1.0))              # short steps: a flood-wait set elsewhere is honoured

    def _take_batch(self) -> list[tuple[str, ConsoleEntry]]:
        """Oldest cards first; as many as fit in one message (usually just one)."""
        batch, size = [], 0
        while self.pending:
            key, entry = self.pending[0]
            n = len(self._card(key, entry)) + 2
            if batch and size + n > MAX_MESSAGE:
                break
            self.pending.popleft()
            batch.append((key, entry))
            size += n
        return batch

    def _card(self, key: str, entry: ConsoleEntry) -> str:
        label, theme = self.label_and_theme(key)
        return build_card(entry, label, theme, self.mask)

    async def _send_loop(self) -> None:
        """One sender. Sends the waiting cards as fast as Telegram allows; when more cards arrive than the
        per-minute limit lets through, several are packed into one message (no waiting, nothing lost)."""
        while True:
            try:
                if not self.pending:
                    self.wake.clear()
                    await self.wake.wait()
                await self._breather()
                await self._rate_wait()
                batch = self._take_batch()
                if not batch:
                    continue
                await self._post("\n\n".join(self._card(k, e) for k, e in batch))
                self._sent_at.append(time.monotonic())
                self._last_send = time.monotonic()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Feed: sender error (continuing)")

    async def _post(self, text: str) -> None:
        for attempt in range(6):
            try:
                await self.bot.send_message(self.channel, text, parse_mode=ParseMode.HTML,
                                            reply_markup=self.markup, disable_web_page_preview=True,
                                            read_timeout=20, write_timeout=20, connect_timeout=10)
                return
            except RetryAfter as e:
                log.info("Feed: Telegram flood-wait, sleeping %ss", e.retry_after)
                self._pause_until = max(self._pause_until, time.monotonic() + e.retry_after + 0.5)
                self._sent_at.clear()
                await asyncio.sleep(e.retry_after + 0.5)
            except (Forbidden, BadRequest) as e:
                log.error("Feed: cannot post to chat: %s", e)
                return
            except TelegramError as e:
                log.warning("Feed: send failed (%s), attempt %d", e, attempt + 1)
                await asyncio.sleep(2)
        log.error("Feed: gave up on one message after 6 attempts")
