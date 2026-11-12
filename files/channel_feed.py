"""
channel_feed.py - live console feed: every new row that appears on either site's console is
posted to a Telegram channel/group as a colourful card.

    Get Number 1 (Voltx)  -> blue card   🟦
    Get Number 2 (Zenex)  -> green card  🟩

Environment variables:
    CHANNEL_ID        required. "-1001234567890" or "@yourchannel". The bot must be in the chat
                      (admin with "Post messages" for a channel).
    FEED_INTERVAL     optional, seconds between checks (default 5)
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
from collections import OrderedDict
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from api_client import ApiError, extract_code
from console_api import ConsoleEntry

log = logging.getLogger("voltx.feed")

SEEN_LIMIT = 5000         # remembered rows per site
MAX_POSTS_PER_CYCLE = 25  # safety cap

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
        self.interval = max(2.0, float(os.getenv("FEED_INTERVAL", "5") or 5))
        self.mask = (os.getenv("FEED_MASK", "1").strip() != "0")
        link = (os.getenv("FEED_BOT_LINK") or "").strip()
        self.markup = (InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Open Bot", url=link)]])
                       if link.startswith("http") else None)
        self.seen: dict[str, OrderedDict[str, None]] = {}
        self.task: asyncio.Task | None = None

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

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()

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
                posted = 0
                for key, entries in zip(keys, results):
                    if entries is None:
                        continue
                    seen = self.seen.setdefault(key, OrderedDict())
                    fresh = [e for e in entries if e.entry_id and e.entry_id not in seen]
                    fresh.sort(key=lambda e: e.time)       # oldest first
                    for e in fresh:
                        self._remember(key, e.entry_id)
                        if posted >= MAX_POSTS_PER_CYCLE:
                            continue
                        await self._post(key, e)
                        posted += 1
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Feed loop error (continuing)")

    async def _post(self, key: str, entry: ConsoleEntry) -> None:
        label, theme = self.label_and_theme(key)
        text = build_card(entry, label, theme, self.mask)
        for attempt in range(2):
            try:
                await self.bot.send_message(self.channel, text, parse_mode=ParseMode.HTML,
                                            reply_markup=self.markup, disable_web_page_preview=True)
                return
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (Forbidden, BadRequest) as e:
                log.error("Feed: cannot post to chat: %s", e)
                return
            except TelegramError as e:
                log.warning("Feed: send failed (%s), attempt %d", e, attempt + 1)
                await asyncio.sleep(2)
