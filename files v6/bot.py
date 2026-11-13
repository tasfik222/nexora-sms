"""
bot.py - Telegram frontend for two SMS sites (inline keyboards only):
    * Voltx (2oo9)  - key in VOLTXSMS_API_KEY
    * Zenex         - key in ZENEX_API_KEY
A site without a key is hidden from the menu; with only one key set the bot skips the "choose a server" step.

requirements.txt:
    python-telegram-bot>=20.7,<22
    httpx>=0.25

Environment variables:
    TELEGRAM_BOT_TOKEN          token from @BotFather
    VOLTXSMS_API_KEY            Voltx / 2oo9 key (header `mauthapi`)
    ZENEX_API_KEY               Zenex key (header `mapikey`)
    CONSOLE_GROUP_LINK          (optional) link opened by the Console button (default https://t.me/nexoraconsole)
    CHANNEL_ID                  channel (-100... or @name) that receives every new SMS from both sites
                                (bot must be channel admin) - see channel_feed.py
    ADMIN_IDS                   comma-separated Telegram user ids of the admins (withdrawals + admin panel)
    BACKUP_CHAT_ID              private channel/group (-100...) where backups are pinned - see backup.py
    WITHDRAW_MIN_TAKA, WITHDRAW_METHODS   (optional) default 50 and "bKash,Nagad,Rocket"
    TRAFFIC_WINDOW_MIN          (optional) minutes of history for "Top Traffic" (default 30) - see traffic.py
    LOCAL_DATA_DIR              (optional) where per-user data is saved (default ./data) - see storage.py

Flow:
    Get Number -> (choose server) -> choose service -> choose range -> number
               -> poll that server's OTP endpoint until a NEW OTP for that number appears
               -> credit the user's balance.
Neither API has a release/cancel call, so "Change number" just abandons the current number
and orders another one from the same range on the same server.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Optional

from telegram import InlineKeyboardButton as Btn
from telegram import BotCommand, CopyTextButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram import KeyboardButton as KBtn
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from api_client import (
    ApiError,
    ApiKeyError,
    Otp,
    Provider,
    build_providers,
    extract_code,
)
from channel_feed import ChannelFeed, detect_service, mask_number
from console_api import ConsoleClient, ConsoleEntry
from otp_format import build_otp_message
from storage import StorageError, UserStore
from settings import DEFAULT_PRICE, MAX_NAME_LEN, MAX_NUMBERS, MAX_PRICE, SettingsStore, range_key
from traffic import TopRange, TrafficTracker, resolve_listed_range
from withdrawals import CANCELLED, PAID, PENDING, REJECTED, WithdrawError, WithdrawStore
from backup import BackupError, BackupManager, read_backup, restore

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("voltx.bot")

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
PAGE_SIZE = 8
# Order of the "Get Number" buttons. Users only see "Get Number 1" / "Get Number 2" - never the site names.
# Swap the two names to swap the buttons.
GET_NUMBER_ORDER = ["voltx", "zenex"]
# Money is stored as whole PAISA (100 paisa = ৳1) so there are no floating-point errors.
# Reward per SMS is NOT fixed any more: the admin sets a default price and optional per-range prices
# (Admin -> 💰 Range prices). Starting default = settings.DEFAULT_PRICE = 30 paisa = ৳0.30.
CURRENCY = "৳"


def _env_taka(name: str, default: str) -> int:
    try:
        return int((Decimal(os.getenv(name) or default) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return int(Decimal(default) * 100)


DEFAULT_ADMIN_IDS = "5776844693"   # used when the ADMIN_IDS environment variable is not set
ADMIN_IDS: set[int] = {int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS") or DEFAULT_ADMIN_IDS) if x.strip().lstrip("-").isdigit()}
WITHDRAW_MIN = _env_taka("WITHDRAW_MIN_TAKA", "50")      # paisa
WITHDRAW_METHODS = [m.strip() for m in (os.getenv("WITHDRAW_METHODS") or "bKash,Nagad,Rocket").split(",") if m.strip()]
FEATURES = {"withdraw": False}                           # switched on at startup when admins + storage are OK


def money(paisa: int) -> str:
    """30 -> '৳0.30', 150 -> '৳1.50', 12345 -> '৳123.45'"""
    return f"{CURRENCY}{paisa // 100}.{paisa % 100:02d}"


def parse_price(text: str) -> Optional[int]:
    """Admin reward amount: '0.5' / '৳1.25' / '2' -> paisa (50 / 125 / 200). None if invalid, > max, or > 2 decimals."""
    try:
        d = Decimal(text.strip().lstrip(CURRENCY).replace(",", "")) * 100
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d != d.to_integral_value() or not (0 <= d <= MAX_PRICE):
        return None
    return int(d)

POLL_INTERVAL = 5       # seconds between automatic checks (/success-otp is cached ~5s)
POLL_TIMEOUT = 1200     # keep waiting up to 20 minutes
CLOCK_SKEW_MS = 30_000  # only used if the "already seen" snapshot failed
DOUBLE_TAP_MS = 4_000   # ignore a 2nd order tap within this time of the 1st


# ----------------------------------------------------------------------------
# In-memory state: one active activation per Telegram user. No database.
# ----------------------------------------------------------------------------
@dataclass
class Slot:
    """One number inside an activation (an order can hold several numbers at once)."""
    phone: str
    no_plus: str
    country: str = ""
    operator: str = ""
    seen_ids: set = field(default_factory=set)
    seen_ok: bool = False
    done: bool = False
    message: str = ""
    code: Optional[str] = None
    credit_line: str = ""


@dataclass
class Activation:
    rid: str                 # range exactly as listed by the provider, e.g. "22501XXX"
    provider: str            # provider key: "voltx" / "zenex"
    chat_id: int
    message_id: int
    order_ms: int
    slots: list = field(default_factory=list)   # list[Slot] - how many comes from the admin setting
    requested: int = 1
    price: int = DEFAULT_PRICE   # paisa per SMS - fixed when the numbers are ordered (admin changes apply to the NEXT order)
    service: str = ""        # service the range was picked from (used for the button emoji)
    range_name: str = ""     # admin's custom name for this range (Admin -> 🏷 Range names), "" = none
    poll_task: Optional[asyncio.Task] = field(default=None, repr=False)

    @property
    def done(self) -> bool:
        """True when every number of this order has received its SMS."""
        return bool(self.slots) and all(x.done for x in self.slots)


ACTIVATIONS: dict[int, Activation] = {}
LOCKS: dict[int, asyncio.Lock] = {}
NUMBER_OWNER: dict[str, int] = {}  # number -> the ONE Telegram user it was allocated to


def user_lock(uid: int) -> asyncio.Lock:
    return LOCKS.setdefault(uid, asyncio.Lock())


def store_of(context: ContextTypes.DEFAULT_TYPE) -> UserStore:
    return context.application.bot_data["store"]


def settings_of(context: ContextTypes.DEFAULT_TYPE) -> SettingsStore:
    return context.application.bot_data["settings"]


def providers_of(app: Application) -> dict[str, Provider]:
    return app.bot_data["providers"]


def enabled_providers(context: ContextTypes.DEFAULT_TYPE) -> list[Provider]:
    return [p for p in providers_of(context.application).values() if p.enabled]


def owner_key(provider: str, no_plus: str) -> str:
    """A number is only unique per provider, so ownership is tracked as 'provider:number'."""
    return f"{provider}:{no_plus}"


# ----------------------------------------------------------------------------
# Keyboards / texts
# ----------------------------------------------------------------------------
def btn(text: str, callback_data: str, style: str | None = None) -> Btn:
    """Inline button. style: "primary" (blue), "success" (green), "danger" (red); None = default grey.
    Coloured buttons need Telegram apps released after 9 Feb 2026; older apps just show normal buttons."""
    if not style:
        return Btn(text, callback_data=callback_data)
    try:
        return Btn(text, callback_data=callback_data, style=style)           # python-telegram-bot >= 22.7
    except TypeError:
        return Btn(text, callback_data=callback_data, api_kwargs={"style": style})  # older versions


def kbtn(text: str, style: str | None = None) -> KBtn:
    if not style:
        return KBtn(text)
    try:
        return KBtn(text, style=style)
    except TypeError:
        return KBtn(text, api_kwargs={"style": style})


BTN_CONSOLE = "🖥 Console"
CONSOLE_ROWS = 8          # how many latest rows the console view shows
CONSOLE_KEYS: list[str] = []   # sites that have a console (filled at startup)


def site_number(key: str) -> int:
    """Voltx -> 1, Zenex -> 2 (same numbering as the Get Number buttons)."""
    return GET_NUMBER_ORDER.index(key) + 1 if key in GET_NUMBER_ORDER else len(GET_NUMBER_ORDER) + 1


CONSOLE_LINK = (os.getenv("CONSOLE_GROUP_LINK") or "https://t.me/nexoraconsole").strip()


def btn_url(text: str, url: str, style: str | None = None) -> Btn:
    """Inline button that opens a link."""
    if not style:
        return Btn(text, url=url)
    try:
        return Btn(text, url=url, style=style)
    except TypeError:
        return Btn(text, url=url, api_kwargs={"style": style})


def console_buttons() -> list:
    """One "Console" button that opens the live console group."""
    return [btn_url(BTN_CONSOLE, CONSOLE_LINK, "success")]


SERVICE_EMOJI = {
    "telegram": "✈️", "whatsapp": "💬", "instagram": "📸", "facebook": "📘", "google": "🔍",
    "gmail": "✉️", "tiktok": "🎵", "twitter": "🐦", "snapchat": "👻", "discord": "🎮",
    "amazon": "📦", "uber": "🚗", "paypal": "💳", "microsoft": "🪟", "apple": "🍎",
    "netflix": "🎬", "viber": "📞",
}


def service_emoji(name: str) -> str:
    low = name.lower()
    return next((e for k, e in SERVICE_EMOJI.items() if k in low), "📱")


# Labels of the always-visible menu under the message box (also used to match incoming messages).
BTN_GET, BTN_BAL, BTN_HELP = "📲 Get Number", "💰 My Balance", "❓ Help"
BTN_TOP = "🔥 Top Traffic"
BTN_SUPPORT = "🆘 Support"
BTN_WITHDRAW, BTN_ADMIN = "💸 Withdraw", "👑 Admin"
SUPPORT_LINK = (os.getenv("SUPPORT_LINK") or "https://t.me/ldrofbtw").strip()
TOP_KEYS: list[str] = []   # sites that can show / order from top-traffic ranges (filled at startup)
MENU_PROVIDERS: list[str] = []  # enabled provider keys in button order (filled at startup)


def get_buttons() -> list[tuple[str, str]]:
    """[(provider_key, label), ...]. One site -> "Get Number"; two sites -> "Get Number 1" / "Get Number 2"."""
    if not MENU_PROVIDERS:
        return [("", BTN_GET)]
    if len(MENU_PROVIDERS) == 1:
        return [(MENU_PROVIDERS[0], BTN_GET)]
    return [(k, f"{BTN_GET} {i}") for i, k in enumerate(MENU_PROVIDERS, 1)]


def inline_get_row() -> list:
    return [btn(lbl, callback_data=(f"prov:{k}" if k else "get_number"), style=("success", "primary")[i % 2])
            for i, (k, lbl) in enumerate(get_buttons())]


def bottom_menu_kb(uid: int | None = None) -> ReplyKeyboardMarkup:
    rows = [[kbtn(lbl, ("success", "primary")[i % 2]) for i, (_, lbl) in enumerate(get_buttons())]]
    rows.append(([kbtn(BTN_TOP, "danger")] if TOP_KEYS else []) + [kbtn(BTN_SUPPORT, "primary")])
    rows.append([kbtn(BTN_BAL, "primary")] + ([kbtn(BTN_WITHDRAW, "success")] if FEATURES["withdraw"] else []))
    rows.append(([kbtn(BTN_CONSOLE, "success")] if console_buttons() else []) + [kbtn(BTN_HELP, "primary")]
                + ([kbtn(BTN_ADMIN, "danger")] if is_admin(uid) and FEATURES["withdraw"] else []))
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True,
                               input_field_placeholder="Choose from the menu below 👇")


def main_menu_kb() -> InlineKeyboardMarkup:
    rows = [inline_get_row()]
    if TOP_KEYS:
        rows.append([btn(BTN_TOP, callback_data="toptraffic", style="danger")])
    if console_buttons():
        rows.append(console_buttons())
    return InlineKeyboardMarkup(rows + [
        [btn(BTN_BAL, callback_data="balance", style="primary")]
        + ([btn(BTN_WITHDRAW, callback_data="withdraw", style="success")] if FEATURES["withdraw"] else []),
        [btn(BTN_HELP, callback_data="help", style="primary")],
        [btn_url(BTN_SUPPORT, SUPPORT_LINK, "primary")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("🏠 Main menu", callback_data="menu")]])


def list_kb(labels: list[str], page: int, item_cb: str, page_cb: str, back_cb: str, back_label: str,
            style: str | None = None) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    rows = [[btn(lbl, callback_data=f"{item_cb}:{start + i}", style=style)]
            for i, lbl in enumerate(labels[start:start + PAGE_SIZE])]
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", callback_data=f"{page_cb}:{page - 1}"))
    if start + PAGE_SIZE < len(labels):
        nav.append(btn("Next ➡️", callback_data=f"{page_cb}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn(back_label, callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)


OTP_GROUP_LINK = (os.getenv("OTP_GROUP_LINK") or "").strip()   # optional: shows an "OTP Group" button


def copy_btn(text: str, copy: str, style: str | None = None) -> Btn:
    """Button that copies `copy` to the clipboard when tapped."""
    if not style:
        return Btn(text, copy_text=CopyTextButton(text=copy))
    try:
        return Btn(text, copy_text=CopyTextButton(text=copy), style=style)
    except TypeError:
        return Btn(text, copy_text=CopyTextButton(text=copy), api_kwargs={"style": style})


def activation_kb(act: Activation) -> InlineKeyboardMarkup:
    emoji = service_emoji(act.service) if act.service else "📱"
    rows = [[copy_btn(f"{emoji} {sl.phone}", sl.phone, "success" if sl.done else "primary")] for sl in act.slots]
    rows.append([btn("🔄 Change Number", callback_data="change", style="danger" if not act.done else "primary"),
                 *console_buttons()])
    if OTP_GROUP_LINK:
        rows.append([btn_url("🔗 OTP Group", OTP_GROUP_LINK, "primary")])
    if act.done:
        rows.append([btn("📲 Pick another range", callback_data=f"prov:{act.provider}", style="success")])
        rows.append([btn("🏠 Main menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def activation_text(act: Activation) -> str:
    first = act.slots[0]
    place = " · ".join(x for x in (first.country, first.operator) if x)
    lines = ["✅ <b>NUMBERS ASSIGNED SUCCESSFULLY!</b>" if len(act.slots) > 1 else "✅ <b>NUMBER ASSIGNED SUCCESSFULLY!</b>", ""]
    if act.range_name:
        lines += [f"🏷 <b>{html.escape(act.range_name)}</b>"]
    if place:
        lines += [f"🌍 <b>{html.escape(place)}</b>"]
    if act.range_name or place:
        lines.append("")
    lines.append("📞 <b>Your Phone Numbers:</b>" if len(act.slots) > 1 else "📞 <b>Your Phone Number:</b>")
    for i, sl in enumerate(act.slots, 1):
        line = f"{i}. <code>{html.escape(sl.phone)}</code>"
        if sl.done:
            line += f"  ✅ <code>{html.escape(sl.code)}</code>" if sl.code else "  ✅ SMS received"
        lines.append(line)
    if len(act.slots) < act.requested:
        lines.append(f"\n<i>Only {len(act.slots)} of {act.requested} numbers were available.</i>")
    lines += ["", f"🪙 <b>Reward:</b> {money(act.price)} Per OTP!", ""]
    if act.done:
        lines.append("✅ <i>All SMS received. Balance updated.</i>")
    else:
        got = sum(1 for x in act.slots if x.done)
        lines.append("⏳ <i>Waiting for incoming SMS...</i>" + (f" ({got}/{len(act.slots)} received)" if got else ""))
    return "\n".join(lines)


HELP_TEXT = (
    "<b>How it works</b>\n\n"
    "1. Tap a <b>Get Number</b> button, then pick a service and a range.\n"
    "2. Use the number on the app you're verifying.\n"
    "3. The bot checks for the SMS every 5 seconds for up to 20 minutes and sends it here automatically.\n"
    "4. Tap <b>Change number</b> to get different number(s) from the same range.\n"
    "5. <b>🔥 Top Traffic</b> shows the 10 busiest ranges right now - tap one to get a number from it.\n"
    "6. Every SMS you receive adds the <b>Reward</b> shown for your range to your balance (see <b>My Balance</b>).\n"
    f"7. <b>💸 Withdraw</b>: fill in the form (method, number, amount; minimum {money(WITHDRAW_MIN)}). "
    "An admin checks it and pays you."
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
async def safe_edit(query, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def show_api_error(query, e: ApiError, retry: str | None = None) -> None:
    (log.error if isinstance(e, ApiKeyError) else log.warning)("API error: %s", e)
    rows = []
    if retry:
        rows.append([btn("🔁 Try again", callback_data=retry, style="primary")])
    rows.append([btn("🏠 Main menu", callback_data="menu")])
    await safe_edit(query, f"⚠️ {html.escape(e.user_message)}", InlineKeyboardMarkup(rows))


def stop_polling(act: Activation) -> None:
    task = act.poll_task
    if task and not task.done() and task is not asyncio.current_task():
        task.cancel()


def clear_activation(uid: int) -> None:
    act = ACTIVATIONS.pop(uid, None)
    if act:
        stop_polling(act)
        for sl in act.slots:
            key = owner_key(act.provider, sl.no_plus)
            if NUMBER_OWNER.get(key) == uid:
                NUMBER_OWNER.pop(key, None)


def fmt_time(ms: int) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ms / 1000)) if ms else "?"


# ----------------------------------------------------------------------------
# Stop / Start (maintenance mode)
# ----------------------------------------------------------------------------
MAINT_TEXT = (
    "🛠 <b>BOT UPDATE IN PROGRESS</b>\n"
    "▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    "✨ We are adding new features and improvements to make the bot even better for you.\n\n"
    "⏳ <b>The bot will be back online in a few minutes.</b>\n\n"
    "💰 Your balance and data are <b>100% safe</b>.\n"
    "🔔 We will message you here the moment we are back.\n\n"
    "🙏 Thank you for your patience!"
)
MAINT_ALERT = "🛠 Bot update in progress. We'll be back online in a few minutes!"
BACK_TEXT = (
    "✅ <b>BOT IS BACK ONLINE!</b>\n"
    "▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    "🚀 The update is complete and the new features are live.\n\n"
    "👇 Your menu is ready below - tap <b>Get Number</b> to continue."
)


def maintenance_on(app: Application) -> bool:
    st = app.bot_data.get("settings")
    return bool(st and st.maintenance)


async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every handler. While the bot is stopped, normal users only get the update notice."""
    if not maintenance_on(context.application):
        return
    user = update.effective_user
    if user is None or is_admin(user.id):
        return                                          # admins keep full access (to press Start, to test)
    q = update.callback_query
    if q is not None:
        try:
            await q.answer(MAINT_ALERT, show_alert=True)
        except TelegramError:
            pass
    elif update.effective_message is not None and update.effective_chat and update.effective_chat.type == "private":
        now = time.monotonic()
        if now - context.user_data.get("maint_ts", 0) > 8:      # don't repeat the notice for every tap
            context.user_data["maint_ts"] = now
            try:
                await update.effective_message.reply_text(MAINT_TEXT, parse_mode=ParseMode.HTML)
            except TelegramError:
                pass
    raise ApplicationHandlerStop


async def broadcast(app: Application, text: str, admin_chat: int, with_menu: bool = False) -> None:
    """Sends `text` to every known user (not admins), slowly, then tells the admin how it went."""
    sent = failed = 0
    for uid in app.bot_data["store"].known_ids():
        if uid in ADMIN_IDS:
            continue
        for _ in range(2):
            try:
                await app.bot.send_message(uid, text, parse_mode=ParseMode.HTML,
                                           reply_markup=bottom_menu_kb(uid) if with_menu else None)
                sent += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (Forbidden, BadRequest):
                failed += 1                              # user blocked the bot / chat not found
                break
            except TelegramError:
                failed += 1
                break
        await asyncio.sleep(0.05)                        # stay well under Telegram's 30 msg/sec limit
    try:
        await app.bot.send_message(admin_chat, f"📣 Notice delivered to <b>{sent}</b> users"
                                   + (f" ({failed} could not be reached)." if failed else "."), parse_mode=ParseMode.HTML)
    except TelegramError:
        pass


# ----------------------------------------------------------------------------
# Menu handlers
# ----------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    try:
        await store_of(context).get(u.id, u.username)
    except StorageError as e:
        log.error("Could not load user %s on /start: %s", u.id, e)  # don't block the menu
    await update.effective_message.reply_text(
        "👋 <b>Welcome!</b>\n\nYour menu is at the bottom of the screen 👇",
        reply_markup=bottom_menu_kb(u.id), parse_mode=ParseMode.HTML,
    )


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await safe_edit(q, "Choose an option:", main_menu_kb())


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await safe_edit(q, HELP_TEXT, back_kb())


async def on_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = q.from_user
    try:
        rec = await store_of(context).get(u.id, u.username)
    except StorageError as e:
        log.error("Could not load user %s for balance: %s", u.id, e)
        await safe_edit(q, "⚠️ Could not load your account right now. Please try again in a moment.", back_kb())
        return
    text = (f"💰 <b>Your balance</b>\n\n{money(rec['balance'])}\n"
            f"Codes received: {rec['total_codes']}")
    latest = rec["history"][-5:][::-1]
    if latest:
        rows = [f"{h['time'][:16].replace('T', ' ')} UTC  +{money(h['amount'])}  <code>{html.escape(h['number'])}</code>"
                for h in latest]
        text += "\n\n<b>Latest</b>\n" + "\n".join(rows)
    await safe_edit(q, text, back_kb())


# ----------------------------------------------------------------------------
# Get-number flow: service -> range -> number
# ----------------------------------------------------------------------------
def services_kb(context, page: int) -> InlineKeyboardMarkup:
    labels = [f"{service_emoji(s.sid)} {s.sid} ({len(s.ranges)})" for s in context.user_data["services"]]
    return list_kb(labels, page, "svc", "svcpage", "menu", "🏠 Main menu", style="primary")


def ranges_kb(context, page: int) -> InlineKeyboardMarkup:
    svc = context.user_data["services"][context.user_data["svc_idx"]]
    st = settings_of(context)
    labels = [f"📞 {st.name_for(r) or lbl}" + (f" · {money(st.price_for(r))}" if st.has_custom_price(r) else "")
              for r, lbl in zip(svc.ranges, svc.labels or svc.ranges)]
    return list_kb(labels, page, "rng", "rngpage", "back_services", "⬅️ Back to services", style="success")


async def on_get_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    enabled = enabled_providers(context)
    if not enabled:
        await safe_edit(q, "⚠️ No SMS server is configured yet. Please contact the bot admin.", back_kb())
        return
    if len(enabled) == 1:  # nothing to choose
        await load_services(q, context, enabled[0].key)
        return
    await safe_edit(q, "Choose an option:", main_menu_kb())


async def on_prov(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    key = q.data.split(":", 1)[1]
    prov = providers_of(context.application).get(key)
    if not prov or not prov.enabled:
        await on_get_number(update, context)
        return
    await load_services(q, context, key)


async def load_services(q, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    await safe_edit(q, "⏳ Loading live services…")
    try:
        services = await providers_of(context.application)[key].get_services()
    except ApiError as e:
        await show_api_error(q, e, retry=f"prov:{key}")
        return
    context.user_data["prov"] = key
    context.user_data["services"] = services
    await safe_edit(q, "Select a service:", services_kb(context, 0))


async def on_back_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not context.user_data.get("services"):
        await on_get_number(update, context)
        return
    await safe_edit(q, "Select a service:", services_kb(context, 0))


async def on_svc_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not context.user_data.get("services"):
        await on_get_number(update, context)
        return
    await safe_edit(q, "Select a service:", services_kb(context, int(q.data.split(":")[1])))


async def on_svc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    services = context.user_data.get("services")
    idx = int(q.data.split(":")[1])
    if not services or idx >= len(services):
        await on_get_number(update, context)
        return
    context.user_data["svc_idx"] = idx
    await safe_edit(q, f"<b>{html.escape(services[idx].sid)}</b>\nSelect a range:", ranges_kb(context, 0))


async def on_rng_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not context.user_data.get("services") or "svc_idx" not in context.user_data:
        await on_get_number(update, context)
        return
    svc = context.user_data["services"][context.user_data["svc_idx"]]
    await safe_edit(q, f"<b>{html.escape(svc.sid)}</b>\nSelect a range:", ranges_kb(context, int(q.data.split(":")[1])))


async def on_rng(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    services = context.user_data.get("services")
    svc_idx = context.user_data.get("svc_idx")
    idx = int(q.data.split(":")[1])
    if not services or svc_idx is None or idx >= len(services[svc_idx].ranges):
        await on_get_number(update, context)
        return
    range_str = services[svc_idx].ranges[idx]
    await order_number(q, context, context.user_data["prov"], range_str, retry=f"rng:{idx}")


async def on_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    act = ACTIVATIONS.get(q.from_user.id)
    if not act:
        await on_get_number(update, context)
        return
    await order_number(q, context, act.provider, act.rid, retry="change")


async def order_number(q, context: ContextTypes.DEFAULT_TYPE, provider: str, rid: str, retry: str) -> None:
    uid = q.from_user.id
    api = providers_of(context.application)[provider]

    async with user_lock(uid):
        existing = ACTIVATIONS.get(uid)
        if existing and not existing.done and int(time.time() * 1000) - existing.order_ms < DOUBLE_TAP_MS:
            await safe_edit(q, activation_text(existing), activation_kb(existing))
            return

        try:
            await store_of(context).get(uid, q.from_user.username)
        except StorageError as e:
            log.error("Could not load user %s before ordering: %s", uid, e)
            await safe_edit(
                q, "⚠️ Could not load your account right now. Please try again in a moment.",
                InlineKeyboardMarkup([[btn("🔁 Try again", callback_data=retry, style="primary")],
                                      [btn("🏠 Main menu", callback_data="menu")]]),
            )
            return

        count = settings_of(context).numbers_per_range      # set by the admin (Admin panel -> Numbers per range)
        price = settings_of(context).price_for(rid)         # set by the admin (Admin panel -> Range prices)
        range_name = settings_of(context).name_for(rid)     # set by the admin (Admin panel -> Range names)
        what = "a number" if count == 1 else f"{count} numbers"
        await safe_edit(q, f"⏳ Getting {what} from <code>{html.escape(range_name or rid)}</code>…")
        order_ms = int(time.time() * 1000)

        slots: list[Slot] = []
        err: Optional[ApiError] = None
        for _ in range(count):
            order = None
            for _try in range(3):               # a repeated number is skipped, ask again
                try:
                    o = await api.get_number(rid)
                except ApiError as e:
                    err = e
                    break
                if all(x.no_plus != o.no_plus_number for x in slots):
                    order = o
                    break
            if order is None:
                break
            slots.append(Slot(phone=order.full_number, no_plus=order.no_plus_number,
                              country=order.country, operator=order.operator))
        if not slots:
            await show_api_error(q, err or ApiError("No number is available in this range right now."), retry=retry)
            return
        if err:
            log.warning("Got %d of %d numbers from %s: %s", len(slots), count, rid, err)

        # Snapshot OTPs already on record so only NEW ones count.
        try:
            existing_otps = await api.get_otps()
            for sl in slots:
                sl.seen_ids = {o.otp_id for o in existing_otps if o.number == sl.no_plus}
                sl.seen_ok = True
        except ApiError as e:
            log.warning("Could not snapshot existing OTPs: %s", e)

        service = ""
        if retry == "change" and existing:
            service = existing.service
        elif retry.startswith("rng:"):
            try:
                service = context.user_data["services"][context.user_data["svc_idx"]].sid
            except (KeyError, IndexError, TypeError):
                pass

        clear_activation(uid)
        act = Activation(
            rid=rid, provider=provider, chat_id=q.message.chat_id, message_id=q.message.message_id,
            order_ms=order_ms, slots=slots, requested=count, price=price, service=service, range_name=range_name,
        )
        ACTIVATIONS[uid] = act
        for sl in slots:
            NUMBER_OWNER[owner_key(provider, sl.no_plus)] = uid
        act.poll_task = context.application.create_task(poll_for_sms(context.application, uid))

    await safe_edit(q, activation_text(act), activation_kb(act))


# ----------------------------------------------------------------------------
# SMS delivery: background polling + manual check
# ----------------------------------------------------------------------------
def find_new_otp(otps: list[Otp], act: Activation, slot: Slot) -> Optional[Otp]:
    fresh = []
    for o in otps:
        if o.number != slot.no_plus:
            continue
        if slot.seen_ok:
            if o.otp_id in slot.seen_ids:
                continue
        elif o.time and o.time < act.order_ms - CLOCK_SKEW_MS:
            continue
        fresh.append(o)
    return max(fresh, key=lambda o: o.time) if fresh else None


async def deliver_otp(app: Application, uid: int, slot: Slot, otp: Otp) -> None:
    act = ACTIVATIONS.get(uid)
    if not act or slot.done or slot not in act.slots:
        return
    if NUMBER_OWNER.get(owner_key(act.provider, slot.no_plus)) != uid:  # number now belongs to someone else
        return
    slot.done = True
    slot.message = otp.message
    slot.code = extract_code(otp.message)
    new_balance = app.bot_data["store"].credit(uid, act.price, slot.phone, slot.code, f"{act.provider}:{otp.otp_id}")
    try:
        await app.bot.edit_message_text(
            activation_text(act), chat_id=act.chat_id, message_id=act.message_id,
            reply_markup=activation_kb(act), parse_mode=ParseMode.HTML,
        )
    except BadRequest as e:
        log.debug("Could not edit panel: %s", e)
    svc_emoji, guess = detect_service(act.service, otp.message)
    svc_name = (act.service.title() if act.service.isupper() else act.service) if act.service else guess
    if act.service:
        svc_emoji = service_emoji(act.service)
    text = build_otp_message(
        number=slot.no_plus, country=slot.country, service_emoji=svc_emoji, service=svc_name,
        code=slot.code, sms=otp.message, reward=money(act.price) if new_balance is not None else None,
        ts_ms=otp.time,
    )
    await app.bot.send_message(act.chat_id, text, parse_mode=ParseMode.HTML)


async def poll_for_sms(app: Application, uid: int) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT
    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            act = ACTIVATIONS.get(uid)
            if not act or act.done:
                return
            if maintenance_on(app) and uid not in ADMIN_IDS:
                continue                                  # bot is stopped: SMS are delivered after it starts again
            pending = [sl for sl in act.slots
                       if not sl.done and NUMBER_OWNER.get(owner_key(act.provider, sl.no_plus)) == uid]
            if not pending:
                return
            try:
                otps = await providers_of(app)[act.provider].get_otps()
            except ApiKeyError as e:
                log.error("Polling stopped, API key problem: %s", e)
                await app.bot.send_message(act.chat_id, f"⚠️ {e.user_message}")
                return
            except ApiError as e:
                log.warning("Poll error (will retry): %s", e)
                continue
            for sl in pending:
                otp = find_new_otp(otps, act, sl)
                if otp:
                    await deliver_otp(app, uid, sl, otp)
            if ACTIVATIONS.get(uid) is not act:      # replaced by a newer order meanwhile
                return
            if act.done:
                return

        act = ACTIVATIONS.get(uid)
        if act and not act.done:
            await app.bot.send_message(
                act.chat_id,
                "⌛ No SMS after 20 minutes. Tap <b>Change number</b> to get new number(s).",
                reply_markup=activation_kb(act),
                parse_mode=ParseMode.HTML,
            )
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Unexpected error in poll task")


# ----------------------------------------------------------------------------
# Bottom menu (reply keyboard): reuse the inline handlers via a small adapter
# ----------------------------------------------------------------------------
class MessageQuery:
    """Looks like a CallbackQuery, but edits a message the bot just sent."""

    def __init__(self, user, message) -> None:
        self.from_user, self.message, self.data = user, message, ""

    async def answer(self, *args, **kwargs) -> None:
        pass

    async def edit_message_text(self, text, **kwargs):
        return await self.message.edit_text(text, **kwargs)


async def _run_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE, handler) -> None:
    placeholder = await update.effective_message.reply_text("⏳ One moment…")
    fake_update = SimpleNamespace(callback_query=MessageQuery(update.effective_user, placeholder))
    await handler(fake_update, context)


async def msg_get_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    label = (getattr(update.effective_message, "text", "") or "").strip()
    key = dict((lbl, k) for k, lbl in get_buttons()).get(label)
    if key is None:  # button from an older keyboard layout: just refresh the menu
        await update.effective_message.reply_text("🔄 Menu updated 👇", reply_markup=bottom_menu_kb(update.effective_user.id))
        return
    if not key:
        await _run_from_message(update, context, on_get_number)
        return
    await _run_from_message(update, context, lambda u, c: load_services(u.callback_query, c, key))


async def msg_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_from_message(update, context, on_balance)


def console_text(key: str, entries: list[ConsoleEntry]) -> str:
    n = site_number(key)
    bar = ("🟦", "🟩", "🟪")[(n - 1) % 3] * 8
    head = f"{bar}\n🖥 <b>Console {n}</b> · live\n{bar}\n"
    if not entries:
        return head + "\n📭 No SMS yet."
    mask = os.getenv("FEED_MASK", "1").strip() != "0"
    rows = []
    for e in entries[:CONSOLE_ROWS]:
        emoji, guess = detect_service(e.service, e.text)
        when = time.strftime("%H:%M:%S", time.localtime(e.time / 1000)) if e.time else ""
        meta = " · ".join(x for x in (e.country, e.operator, e.service or guess) if x)
        number = mask_number(e.number) if mask else e.number
        text = e.text.strip().replace("\n", " ")
        text = text[:70] + "…" if len(text) > 70 else text
        rows.append(f"{emoji} <b>{html.escape(meta)}</b>\n"
                    f"☎️ <code>{html.escape(number)}</code>" + (f"  🕒 {when}" if when else "") +
                    f"\n<i>{html.escape(text)}</i>")
    return head + "\n" + "\n\n".join(rows)


async def on_console(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    key = q.data.split(":", 1)[1]
    client: ConsoleClient = context.application.bot_data["console"]
    if not client.available(key):
        await safe_edit(q, "⚠️ This console is not available.", back_kb())
        return
    try:
        entries = await client.fetch(key)
    except ApiError as e:
        await show_api_error(q, e, retry=f"console:{key}")
        return
    kb = InlineKeyboardMarkup([
        [btn("🔄 Refresh", callback_data=f"console:{key}", style="primary")],
        [btn("🏠 Main menu", callback_data="menu")],
    ])
    await safe_edit(q, console_text(key, entries), kb)


# ----------------------------------------------------------------------------
# Top Traffic: the 10 busiest ranges right now -> tap one to get a number
# ----------------------------------------------------------------------------
TOP_N = 10


def top_server_name(key: str) -> str:
    return f"Get Number {site_number(key)}" if len(TOP_KEYS) > 1 else "Live"


def top_line(i: int, t: TopRange, st: Optional[SettingsStore] = None) -> str:
    svc = t.service.title() if t.service.isupper() else t.service
    nm = st.name_for(t.range) if st is not None else ""
    parts = [f"{i}. <b>{html.escape(nm)}</b>" if nm else f"{i}. <code>{html.escape(t.range)}</code>"]
    if svc:
        parts.append(f"{service_emoji(svc)} {html.escape(svc)}")
    if t.country:
        parts.append(f"🌍 {html.escape(t.country)}")
    if st is not None and st.has_custom_price(t.range):
        parts.append(f"🪙 {money(st.price_for(t.range))}")
    return " · ".join(parts) + f" — <b>{t.count} SMS</b>"


def build_top_view(context: ContextTypes.DEFAULT_TYPE, key: str) -> tuple[str, InlineKeyboardMarkup]:
    tracker: TrafficTracker = context.application.bot_data["traffic"]
    top = tracker.top(key, TOP_N)
    context.user_data.setdefault("top_list", {})[key] = [t.range for t in top]
    head = f"🔥 <b>Top Traffic</b> · {top_server_name(key)}\n"
    nav = [[btn("🔄 Refresh", callback_data=f"toplist:{key}", style="primary")]]
    if len(TOP_KEYS) > 1:
        nav.append([btn("↔️ Other server", callback_data="toptraffic")])
    nav.append([btn("🏠 Main menu", callback_data="menu")])
    if not top:
        msg = ("Collecting live traffic data…" if not tracker.ready.get(key)
               else "No SMS seen in the last %d minutes." % tracker.window_min)
        return head + "\n" + msg + "\nTry again in a minute.", InlineKeyboardMarkup(nav)
    text = (head + f"Busiest ranges in the last {tracker.window_min} min:\n\n"
            + "\n".join(top_line(i, t, settings_of(context)) for i, t in enumerate(top, 1))
            + "\n\nTap a range to get a number from it 👇")
    st = settings_of(context)
    rows = [[btn(f"{i}. {st.name_for(t.range) or t.range} · {t.count}", callback_data=f"topgo:{key}:{i - 1}", style="success")]
            for i, t in enumerate(top, 1)]
    return text, InlineKeyboardMarkup(rows + nav)


def top_choose_kb() -> InlineKeyboardMarkup:
    rows = [[btn(f"🔥 {top_server_name(k)}", callback_data=f"toplist:{k}",
                 style=("success", "primary")[i % 2])] for i, k in enumerate(TOP_KEYS)]
    return InlineKeyboardMarkup(rows + [[btn("🏠 Main menu", callback_data="menu")]])


async def msg_top_traffic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not TOP_KEYS or "traffic" not in context.application.bot_data:
        await update.effective_message.reply_text("🔥 Top Traffic is not available right now.")
        return
    if len(TOP_KEYS) > 1:
        await update.effective_message.reply_text("🔥 <b>Top Traffic</b>\nChoose a server:",
                                                  reply_markup=top_choose_kb(), parse_mode=ParseMode.HTML)
        return
    text, kb = build_top_view(context, TOP_KEYS[0])
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def on_top_traffic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not TOP_KEYS or "traffic" not in context.application.bot_data:
        await safe_edit(q, "🔥 Top Traffic is not available right now.", back_kb())
    elif len(TOP_KEYS) > 1:
        await safe_edit(q, "🔥 <b>Top Traffic</b>\nChoose a server:", top_choose_kb())
    else:
        text, kb = build_top_view(context, TOP_KEYS[0])
        await safe_edit(q, text, kb)


async def on_top_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    key = q.data.split(":", 1)[1]
    if key not in TOP_KEYS or "traffic" not in context.application.bot_data:
        await on_top_traffic(update, context)
        return
    text, kb = build_top_view(context, key)
    await safe_edit(q, text, kb)


async def on_top_go(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, key, idx_s = q.data.split(":")
    idx = int(idx_s)
    lst = context.user_data.get("top_list", {}).get(key) or []
    prov = providers_of(context.application).get(key)
    back = InlineKeyboardMarkup([[btn("🔥 Top Traffic", callback_data=f"toplist:{key}", style="primary")],
                                 [btn("🏠 Main menu", callback_data="menu")]])
    if idx >= len(lst) or not prov or not prov.enabled:
        await safe_edit(q, "⌛ That list is out of date. Open Top Traffic again.", back)
        return
    await safe_edit(q, "⏳ Checking range…")
    try:
        rid = await resolve_listed_range(prov, lst[idx])
    except ApiError as e:
        await show_api_error(q, e, retry=f"topgo:{key}:{idx}")
        return
    if not rid:
        await safe_edit(q, f"⚠️ <code>{html.escape(lst[idx])}</code> is not open for ordering right now. "
                           "Please pick another range.", back)
        return
    await order_number(q, context, key, rid, retry=f"topgo:{key}:{idx}")


async def msg_console(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🖥 <b>Live console</b>\nTap the button to open the console group 👇",
        reply_markup=InlineKeyboardMarkup([console_buttons()]), parse_mode=ParseMode.HTML)


async def msg_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🆘 <b>Support</b>\nNeed help? Tap the button to message our support 👇",
        reply_markup=InlineKeyboardMarkup([[btn_url("💬 Contact Support", SUPPORT_LINK, "primary")]]),
        parse_mode=ParseMode.HTML)


async def msg_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=bottom_menu_kb(update.effective_user.id))


# ----------------------------------------------------------------------------
# Withdrawals (user form) + admin panel
# ----------------------------------------------------------------------------
STATUS_LABEL = {PENDING: "⏳ Pending", PAID: "✅ Paid", REJECTED: "❌ Rejected", CANCELLED: "🚫 Cancelled"}
PHONE_METHODS = {"bkash", "nagad", "rocket", "upay"}


def is_admin(uid: int | None) -> bool:
    return uid in ADMIN_IDS


def wd_of(context: ContextTypes.DEFAULT_TYPE) -> WithdrawStore:
    return context.application.bot_data["withdrawals"]


def fmt_utc(iso: str | None) -> str:
    return (iso[:16].replace("T", " ") + " UTC") if iso else "?"


def normalize_account(method: str, text: str) -> Optional[str]:
    """bKash / Nagad / Rocket / Upay need a mobile number; any other method accepts free text."""
    text = text.strip()
    if method.lower() in PHONE_METHODS:
        d = re.sub(r"\D", "", text)
        if d.startswith("880") and len(d) in (13, 14):
            d = "0" + d[3:]
        return d if re.fullmatch(r"01\d{9,10}", d) else None      # 11 digits (Rocket: 12)
    return text if 4 <= len(text) <= 60 else None


def parse_taka(text: str) -> Optional[int]:
    t = re.sub(r"(?i)৳|tk|taka|,", "", text).strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", t):
        return None
    return int((Decimal(t) * 100).to_integral_value())


def req_text(context: ContextTypes.DEFAULT_TYPE, r: dict, admin_view: bool) -> str:
    lines = [f"💸 <b>Withdrawal #{r['id']}</b> · {STATUS_LABEL.get(r['status'], r['status'])}", ""]
    if admin_view:
        who = html.escape(r.get("full_name") or "User")
        uname = f" (@{html.escape(r['username'])})" if r.get("username") else ""
        lines.append(f'👤 <a href="tg://user?id={r["user_id"]}">{who}</a>{uname} · <code>{r["user_id"]}</code>')
    lines += [f"💳 {html.escape(r['method'])} · <code>{html.escape(r['account'])}</code>",
              f"💰 <b>{money(r['amount'])}</b>", f"🕒 {fmt_utc(r['created_at'])}"]
    if admin_view:
        rec = store_of(context).peek(r["user_id"])
        if rec:
            lines.append(f"📊 Balance now: {money(rec['balance'])} · Codes: {rec['total_codes']}")
    if r["status"] != PENDING and r.get("decided_at"):
        lines.append(f"📌 {fmt_utc(r['decided_at'])}")
    if r.get("reason"):
        lines.append(f"📝 {html.escape(r['reason'])}")
    return "\n".join(lines)


async def notify_admins(app: Application, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    for aid in ADMIN_IDS:
        try:
            await app.bot.send_message(aid, text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except TelegramError as e:
            log.warning("Could not notify admin %s (has this admin pressed /start?): %s", aid, e)


def backup_soon(app: Application) -> None:
    bm = app.bot_data.get("backup")
    if bm:
        bm.request_soon()


def menu_labels() -> set[str]:
    return {BTN_BAL, BTN_HELP, BTN_CONSOLE, BTN_TOP, BTN_SUPPORT, BTN_WITHDRAW, BTN_ADMIN} | {l for _, l in get_buttons()}


# ---- user side --------------------------------------------------------------
async def wd_home_view(context: ContextTypes.DEFAULT_TYPE, user) -> tuple[str, InlineKeyboardMarkup]:
    rec = await store_of(context).get(user.id, user.username)
    bal, wd = rec["balance"], wd_of(context)
    pend = wd.pending_for(user.id)
    text = f"💸 <b>Withdraw</b>\n\nBalance: <b>{money(bal)}</b>\nMinimum: {money(WITHDRAW_MIN)}"
    rows = []
    if pend:
        text += "\n\n⏳ <b>Waiting for admin review:</b>\n\n" + req_text(context, pend, False)
        rows.append([btn(f"🚫 Cancel request #{pend['id']}", callback_data=f"wdc:{pend['id']}", style="danger")])
    elif bal >= WITHDRAW_MIN:
        rows.append([btn("📝 New withdrawal", callback_data="wd:new", style="success")])
    else:
        text += f"\n\nYou need {money(WITHDRAW_MIN - bal)} more to withdraw."
    rows.append([btn("📜 My withdrawals", callback_data="wd:hist")])
    rows.append([btn("🏠 Main menu", callback_data="menu")])
    return text, InlineKeyboardMarkup(rows)


def wd_unavailable_kb() -> InlineKeyboardMarkup:
    return back_kb()


async def on_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    context.user_data.pop("wd", None)
    if not FEATURES["withdraw"]:
        await safe_edit(q, "💸 Withdrawals are not available right now.", back_kb())
        return
    try:
        text, kb = await wd_home_view(context, q.from_user)
    except StorageError as e:
        log.error("Could not load user %s for withdraw: %s", q.from_user.id, e)
        await safe_edit(q, "⚠️ Could not load your account right now. Please try again in a moment.", back_kb())
        return
    await safe_edit(q, text, kb)


async def msg_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_from_message(update, context, on_withdraw)


def wd_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("✖ Cancel", callback_data="wd:abort", style="danger")]])


def wd_confirm_view(context: ContextTypes.DEFAULT_TYPE, user_id: int, st: dict) -> tuple[str, InlineKeyboardMarkup]:
    rec = store_of(context).peek(user_id) or {"balance": 0}
    text = (f"📝 <b>Confirm withdrawal</b>\n\n💳 {html.escape(st['method'])} · <code>{html.escape(st['account'])}</code>\n"
            f"💰 <b>{money(st['amount'])}</b>\nBalance after: {money(rec['balance'] - st['amount'])}\n\n"
            "Check the number carefully - a wrong number can't be recovered.")
    return text, InlineKeyboardMarkup([[btn("✅ Confirm", callback_data="wd:ok", style="success"),
                                        btn("✖ Cancel", callback_data="wd:abort", style="danger")]])


async def on_wd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    action = q.data.split(":")[1]
    uid = q.from_user.id
    if not FEATURES["withdraw"]:
        await safe_edit(q, "💸 Withdrawals are not available right now.", back_kb())
        return
    wd = wd_of(context)

    if action == "abort":
        await on_withdraw(update, context)
        return

    if action == "hist":
        rows = wd.list(uid=uid, limit=8)
        text = "📜 <b>My withdrawals</b>\n\n" + ("\n".join(
            f"#{r['id']} · {money(r['amount'])} · {html.escape(r['method'])} · {STATUS_LABEL[r['status']]} · "
            f"{fmt_utc(r['created_at'])[:10]}" for r in rows) if rows else "No withdrawals yet.")
        await safe_edit(q, text, InlineKeyboardMarkup([[btn("⬅️ Back", callback_data="withdraw")]]))
        return

    if action == "new":
        try:
            rec = await store_of(context).get(uid, q.from_user.username)
        except StorageError:
            await safe_edit(q, "⚠️ Could not load your account right now. Please try again.", back_kb())
            return
        if wd.pending_for(uid) or rec["balance"] < WITHDRAW_MIN:
            await on_withdraw(update, context)
            return
        context.user_data["wd"] = {"step": "method"}
        rows = [[btn(f"💳 {m}", callback_data=f"wdm:{i}", style="primary")] for i, m in enumerate(WITHDRAW_METHODS)]
        rows.append([btn("✖ Cancel", callback_data="wd:abort", style="danger")])
        await safe_edit(q, "📝 <b>Withdrawal form</b>\n\nStep 1/3 - choose how you want to be paid:", InlineKeyboardMarkup(rows))
        return

    st = context.user_data.get("wd")
    if action == "all":
        if not st or st.get("step") != "amount":
            await on_withdraw(update, context)
            return
        rec = store_of(context).peek(uid)
        if not rec or rec["balance"] < WITHDRAW_MIN:
            await on_withdraw(update, context)
            return
        st["amount"], st["step"] = rec["balance"], "confirm"
        text, kb = wd_confirm_view(context, uid, st)
        await safe_edit(q, text, kb)
        return

    if action == "ok":
        st = context.user_data.pop("wd", None)           # popped first: a double tap can't submit twice
        if not st or st.get("step") != "confirm":
            await safe_edit(q, "⌛ This form has expired. Please start again.",
                            InlineKeyboardMarkup([[btn("💸 Withdraw", callback_data="withdraw", style="primary")]]))
            return
        try:
            await store_of(context).get(uid, q.from_user.username)
            r = wd.create(uid, q.from_user.username or "", q.from_user.full_name or "", st["method"],
                          st["account"], st["amount"], WITHDRAW_MIN)
        except WithdrawError as e:
            await safe_edit(q, f"⚠️ {html.escape(e.user_message)}", back_kb())
            return
        except StorageError:
            await safe_edit(q, "⚠️ Could not load your account right now. Please try again.", back_kb())
            return
        backup_soon(context.application)
        await safe_edit(q, f"✅ <b>Request #{r['id']} sent</b>\n\n{money(r['amount'])} → {html.escape(r['method'])} "
                           f"<code>{html.escape(r['account'])}</code>\n\nThe amount was taken from your balance. "
                           "You'll get a message when an admin pays or rejects it (rejected = money returned).",
                        InlineKeyboardMarkup([[btn("📜 My withdrawals", callback_data="wd:hist")],
                                              [btn("🏠 Main menu", callback_data="menu")]]))
        await notify_admins(context.application, "🔔 <b>New withdrawal request</b>\n\n" + req_text(context, r, True),
                            InlineKeyboardMarkup([[btn("✅ Mark paid", callback_data=f"adm:pay:{r['id']}", style="success"),
                                                   btn("❌ Reject", callback_data=f"adm:rej:{r['id']}", style="danger")],
                                                  [btn("📥 All pending", callback_data="adm:pend")]]))


async def on_wd_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    st = context.user_data.get("wd")
    idx = int(q.data.split(":")[1])
    if not FEATURES["withdraw"] or not st or idx >= len(WITHDRAW_METHODS):
        await on_withdraw(update, context)
        return
    st.update(step="account", method=WITHDRAW_METHODS[idx])
    hint = "your mobile number, e.g. <code>01XXXXXXXXX</code>" if st["method"].lower() in PHONE_METHODS \
        else "your account / wallet details"
    await safe_edit(q, f"📝 <b>Withdrawal form</b>\n\nStep 2/3 - send {hint} for <b>{html.escape(st['method'])}</b>:",
                    wd_cancel_kb())


async def on_wd_cancel_req(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not FEATURES["withdraw"]:
        return
    req_id = int(q.data.split(":")[1])
    try:
        await store_of(context).get(q.from_user.id, q.from_user.username)
        r = wd_of(context).cancel(req_id, q.from_user.id)
    except WithdrawError as e:
        await safe_edit(q, f"⚠️ {html.escape(e.user_message)}",
                        InlineKeyboardMarkup([[btn("💸 Withdraw", callback_data="withdraw", style="primary")]]))
        return
    except StorageError:
        await safe_edit(q, "⚠️ Could not load your account right now. Please try again.", back_kb())
        return
    backup_soon(context.application)
    await safe_edit(q, f"🚫 Request #{r['id']} cancelled. {money(r['amount'])} is back in your balance.",
                    InlineKeyboardMarkup([[btn("💸 Withdraw", callback_data="withdraw", style="primary")],
                                          [btn("🏠 Main menu", callback_data="menu")]]))


async def apply_decision(app: Application, admin_id: int, req_id: int, status: str,
                         reason: str = "") -> tuple[Optional[dict], str]:
    """Pay or reject a request and tell the user. Returns (request, "") or (None, error text)."""
    wd: WithdrawStore = app.bot_data["withdrawals"]
    r = wd.get(req_id)
    if not r:
        return None, "Request not found."
    try:
        await app.bot_data["store"].get(r["user_id"])    # the refund needs the user's record loaded
        r = wd.mark_paid(req_id, admin_id) if status == PAID else wd.reject(req_id, admin_id, reason)
    except StorageError:
        return None, "Could not load the user's record. Try again."
    except WithdrawError as e:
        return None, e.user_message
    backup_soon(app)
    if status == PAID:
        msg = (f"✅ <b>Withdrawal #{r['id']} paid</b>\n\n{money(r['amount'])} was sent to "
               f"{html.escape(r['method'])} <code>{html.escape(r['account'])}</code>.")
    else:
        msg = (f"❌ <b>Withdrawal #{r['id']} rejected</b>\n\n{money(r['amount'])} was returned to your balance."
               + (f"\nReason: {html.escape(reason)}" if reason else ""))
    try:
        await app.bot.send_message(r["user_id"], msg, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        log.warning("Could not notify user %s about withdrawal %s: %s", r["user_id"], r["id"], e)
    return r, ""


async def handle_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, text: str) -> None:
    """An admin typed a price: either the default price, or lines of '<range> [<range> ...] <taka>'."""
    msg, st = update.effective_message, settings_of(context)
    again = InlineKeyboardMarkup([[btn("⬅️ Back", callback_data="adm:price")]])
    if isinstance(mode, dict):                      # picked from the range list
        rng = mode.get("range", "")
        back = InlineKeyboardMarkup([[btn("⬅️ Back to ranges", callback_data=f"adm:ppg:{mode.get('key', '')}:{mode.get('sv', 0)}:{mode.get('page', 0)}")]])
        paisa = parse_price(text)
        if paisa is None or not st.set_range_price(rng, paisa):
            await msg.reply_text(f"⚠️ Send a valid amount in taka, like 0.50 (max 2 decimals, up to {money(MAX_PRICE)}).",
                                 reply_markup=back)
            return
        context.user_data.pop("adm_price", None)
        await msg.reply_text(f"✅ <code>{html.escape(range_key(rng))}</code> → <b>{money(paisa)}</b> per SMS\n\n"
                             "Applies to the next number ordered.", reply_markup=back, parse_mode=ParseMode.HTML)
        return
    if mode == "default":
        paisa = parse_price(text)
        if paisa is None or not st.set_default_price(paisa):
            await msg.reply_text(f"⚠️ Send a valid amount in taka, like 0.30 (max 2 decimals, up to {money(MAX_PRICE)}).",
                                 reply_markup=again)
            return
        context.user_data.pop("adm_price", None)
        out, kb = price_home_view(context, f"✅ Default reward is now <b>{money(paisa)}</b>.")
        await msg.reply_text(out, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    done: list[str] = []
    bad: list[str] = []
    for line in text.splitlines():
        toks = [t for t in re.split(r"[\s=:,]+", line.strip()) if t]
        if not toks:
            continue
        paisa = parse_price(toks[-1]) if len(toks) >= 2 else None
        rngs = [range_key(t) for t in toks[:-1]]
        if paisa is None or not all(re.fullmatch(r"[0-9A-Za-z]+", r) for r in rngs) or not all(rngs):
            bad.append(line.strip())
            continue
        for r in rngs:
            if st.set_range_price(r, paisa):
                done.append(f"<code>{html.escape(r)}</code> → {money(paisa)}")
            else:
                bad.append(f"{r} {toks[-1]}")
    if not done:
        await msg.reply_text("⚠️ I could not read that. Send like this:\n<code>22501XXX 0.50</code>",
                             reply_markup=again, parse_mode=ParseMode.HTML)
        return
    context.user_data.pop("adm_price", None)
    note = "✅ <b>Saved</b>\n" + "\n".join(done[:30])
    if bad:
        note += "\n\n⚠️ Skipped (could not read):\n" + "\n".join(f"<code>{html.escape(b[:40])}</code>" for b in bad[:10])
    out, kb = price_home_view(context, note)
    await msg.reply_text(out, reply_markup=kb, parse_mode=ParseMode.HTML)


async def handle_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """The admin picked a range from the list and typed its new name."""
    msg, st = update.effective_message, settings_of(context)
    sel = context.user_data.get("adm_name") or {}
    rng = sel.get("range", "")
    back = InlineKeyboardMarkup([[btn("⬅️ Back to ranges", callback_data=f"adm:npg:{sel.get('key', '')}:{sel.get('sv', 0)}:{sel.get('page', 0)}")]])
    if not rng or not st.set_range_name(rng, text):
        await msg.reply_text("⚠️ Send a name (text, up to %d characters)." % MAX_NAME_LEN, reply_markup=back)
        return
    context.user_data.pop("adm_name", None)
    await msg.reply_text(f"✅ <code>{html.escape(range_key(rng))}</code> → <b>{html.escape(st.name_for(rng))}</b>\n\n"
                         "Users see this name now.", reply_markup=back, parse_mode=ParseMode.HTML)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Typed answers: the withdrawal form (steps 2 and 3) and an admin's rejection reason."""
    msg, user = update.effective_message, update.effective_user
    text = (msg.text or "").strip()
    if not text:
        return
    if text in menu_labels():                       # the user pressed a menu button: abandon any form
        context.user_data.pop("wd", None)
        context.user_data.pop("adm_reject", None)
        context.user_data.pop("adm_price", None)
        context.user_data.pop("adm_name", None)
        return

    name_mode = context.user_data.get("adm_name")
    if name_mode and is_admin(user.id):
        await handle_name_input(update, context, text)
        return

    price_mode = context.user_data.get("adm_price")
    if price_mode and is_admin(user.id):
        await handle_price_input(update, context, price_mode, text)
        return

    req_id = context.user_data.get("adm_reject")
    if req_id is not None and is_admin(user.id):
        context.user_data.pop("adm_reject", None)
        r, err = await apply_decision(context.application, user.id, req_id, REJECTED, text[:200])
        await msg.reply_text(f"⚠️ {html.escape(err)}" if err else f"❌ Request #{req_id} rejected, money returned.",
                             reply_markup=InlineKeyboardMarkup([[btn("📥 Pending", callback_data="adm:pend")]]),
                             parse_mode=ParseMode.HTML)
        return

    st = context.user_data.get("wd")
    if not st or not FEATURES["withdraw"]:
        return
    if st["step"] == "account":
        acct = normalize_account(st["method"], text)
        if not acct:
            await msg.reply_text("⚠️ That doesn't look right. Send a valid mobile number like 01XXXXXXXXX.",
                                 reply_markup=wd_cancel_kb())
            return
        st.update(account=acct, step="amount")
        rec = await store_of(context).get(user.id, user.username)
        await msg.reply_text(
            f"📝 <b>Withdrawal form</b>\n\nStep 3/3 - how much? Send an amount in taka "
            f"(min {money(WITHDRAW_MIN)}, max {money(rec['balance'])}).",
            reply_markup=InlineKeyboardMarkup([[btn(f"💯 All {money(rec['balance'])}", callback_data="wd:all", style="success")],
                                               [btn("✖ Cancel", callback_data="wd:abort", style="danger")]]),
            parse_mode=ParseMode.HTML)
    elif st["step"] == "amount":
        amount = parse_taka(text)
        rec = await store_of(context).get(user.id, user.username)
        if amount is None:
            await msg.reply_text("⚠️ Send just the number, e.g. 100 or 150.50", reply_markup=wd_cancel_kb())
            return
        if amount < WITHDRAW_MIN or amount > rec["balance"]:
            await msg.reply_text(f"⚠️ The amount must be between {money(WITHDRAW_MIN)} and {money(rec['balance'])}.",
                                 reply_markup=wd_cancel_kb())
            return
        st.update(amount=amount, step="confirm")
        out, kb = wd_confirm_view(context, user.id, st)
        await msg.reply_text(out, reply_markup=kb, parse_mode=ParseMode.HTML)


# ---- admin side -------------------------------------------------------------
def admin_home_view(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    us, ws = store_of(context).stats(), wd_of(context).stats()
    bm: BackupManager = context.application.bot_data["backup"]
    text = ("👑 <b>Admin panel</b>\n\n"
            f"👥 Users: <b>{us['users']}</b> · SMS codes: {us['codes']}\n"
            f"💰 Users' balances: <b>{money(us['balance'])}</b>\n\n"
            f"⏳ Pending: <b>{ws[PENDING]['count']}</b> ({money(ws[PENDING]['amount'])})\n"
            f"✅ Paid: {ws[PAID]['count']} ({money(ws[PAID]['amount'])})\n"
            f"❌ Rejected: {ws[REJECTED]['count']} · 🚫 Cancelled: {ws[CANCELLED]['count']}\n\n"
            f"🔢 Numbers per range: <b>{settings_of(context).numbers_per_range}</b>\n"
            f"💰 Default reward: <b>{money(settings_of(context).default_price)}</b> · "
            f"custom ranges: <b>{len(settings_of(context).range_prices)}</b>\n"
            f"🏷 Named ranges: <b>{len(settings_of(context).range_names)}</b>\n\n"
            f"{bm.status_line()}\n\n"
            + ("🔴 Bot status: <b>STOPPED</b> (users see the update notice)" if settings_of(context).maintenance
               else "🟢 Bot status: <b>RUNNING</b>"))
    on = not settings_of(context).maintenance
    kb = InlineKeyboardMarkup([
        [btn("🛑 Stop" + (" ✅" if not on else ""), callback_data="adm:stop", style="danger" if on else "primary"),
         btn("▶️ Start" + (" ✅" if on else ""), callback_data="adm:start", style="success" if not on else "primary")],
        [btn(f"📥 Pending ({ws[PENDING]['count']})", callback_data="adm:pend", style="danger")],
        [btn(f"🔢 Numbers per range ({settings_of(context).numbers_per_range})", callback_data="adm:num", style="success")],
        [btn("💰 Range prices", callback_data="adm:price", style="success"),
         btn("🏷 Range names", callback_data="adm:name", style="success")],
        [btn("✅ Paid", callback_data="adm:hist:paid", style="success"),
         btn("❌ Rejected", callback_data="adm:hist:rejected", style="primary")],
        [btn("💾 Backup now", callback_data="adm:backup", style="primary"),
         btn("🔄 Refresh", callback_data="adm:home")],
        [btn("🏠 Main menu", callback_data="menu")],
    ])
    return text, kb


def price_home_view(context: ContextTypes.DEFAULT_TYPE, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    st = settings_of(context)
    prices = sorted(st.range_prices.items())
    lines = [f"  <code>{html.escape(k)}</code> → {money(v)}" for k, v in prices[:15]]
    if len(prices) > 15:
        lines.append(f"  … and {len(prices) - 15} more")
    text = ((note + "\n\n") if note else "") + (
        "💰 <b>Range prices</b>\n\n"
        f"Default reward: <b>{money(st.default_price)}</b> per SMS\n"
        f"Custom ranges: <b>{len(prices)}</b>\n"
        + ("\n" + "\n".join(lines) + "\n" if lines else "")
        + "\nA range with a custom price pays that amount per SMS; every other range pays the default. "
          "Changes apply to the next number ordered.")
    kb = InlineKeyboardMarkup([
        [btn("➕ Set range price", callback_data="adm:psel", style="success")],
        [btn("✍️ Type many at once", callback_data="adm:pset", style="primary")],
        [btn(f"💵 Default price ({money(st.default_price)})", callback_data="adm:pdef", style="primary"),
         btn(f"📋 Custom list ({len(prices)})", callback_data="adm:plist", style="primary")],
        [btn("⬅️ Back", callback_data="adm:home")],
    ])
    return text, kb


def name_servers(context: ContextTypes.DEFAULT_TYPE) -> list[tuple[str, str]]:
    """[(provider_key, 'Get Number N')] for the enabled servers."""
    keys = {p.key for p in enabled_providers(context)}
    out = [(k, lbl) for k, lbl in get_buttons() if k in keys]
    return out or [(p.key, p.key.title()) for p in enabled_providers(context)]


def name_home_view(context: ContextTypes.DEFAULT_TYPE, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    st = settings_of(context)
    text = ((note + "\n\n") if note else "") + (
        "🏷 <b>Range names</b>\n\n"
        f"Named ranges: <b>{len(st.range_names)}</b>\n\n"
        "Choose a server, then tap the range you want to rename 👇")
    rows = [[btn(f"🏷 {lbl}", callback_data=f"adm:nsrv:{k}", style=("success", "primary")[i % 2])]
            for i, (k, lbl) in enumerate(name_servers(context))]
    rows.append([btn(f"📋 Named list ({len(st.range_names)})", callback_data="adm:nlist", style="primary")])
    rows.append([btn("⬅️ Back", callback_data="adm:home")])
    return text, InlineKeyboardMarkup(rows)


NAME_PAGE = 10


def price_servers_view(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    rows = [[btn(f"💰 {lbl}", callback_data=f"adm:psrv:{k}", style=("success", "primary")[i % 2])]
            for i, (k, lbl) in enumerate(name_servers(context))]
    rows.append([btn("⬅️ Back", callback_data="adm:price")])
    return "💰 <b>Set range price</b>\n\nChoose a server, then tap the range you want to price 👇", InlineKeyboardMarkup(rows)


def _svc_cache(context, key: str) -> list:
    return context.user_data.get("adm_svc", {}).get(key) or []


def _nav(rows: list, page: int, pages: int, cb) -> None:
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", callback_data=cb(page - 1)))
    if page + 1 < pages:
        nav.append(btn("Next ➡️", callback_data=cb(page + 1)))
    if nav:
        rows.append(nav)


def admin_services_view(context: ContextTypes.DEFAULT_TYPE, key: str, page: int,
                        mode: str) -> tuple[str, InlineKeyboardMarkup]:
    """Step 2 of the admin range picker: the services (Facebook, WhatsApp, ...) of one server.
    mode "n" = rename ranges, "p" = set range prices."""
    st = settings_of(context)
    items = _svc_cache(context, key)
    pages = max(1, -(-len(items) // NAME_PAGE))
    page = max(0, min(page, pages - 1))
    rows = [[btn(f"{service_emoji(sid)} {sid} ({len(rngs)})", callback_data=f"adm:{mode}sv:{key}:{page * NAME_PAGE + i}",
                 style="primary")]
            for i, (sid, rngs) in enumerate(items[page * NAME_PAGE:(page + 1) * NAME_PAGE])]
    _nav(rows, page, pages, lambda pg: f"adm:{mode}sp:{key}:{pg}")
    rows.append([btn("⬅️ Back", callback_data="adm:psel" if mode == "p" else "adm:name")])
    lbl = dict(name_servers(context)).get(key, key)
    head = "💰 <b>Range prices</b>" if mode == "p" else "🏷 <b>Range names</b>"
    return (f"{head} · {html.escape(lbl)}\n\nSelect a service:", InlineKeyboardMarkup(rows))


def name_ranges_view(context: ContextTypes.DEFAULT_TYPE, key: str, sv: int, page: int,
                     mode: str = "n") -> tuple[str, InlineKeyboardMarkup]:
    """Step 3: the ranges of one service. Tap one to rename it / set its price."""
    st = settings_of(context)
    sid, items = _svc_cache(context, key)[sv]
    pages = max(1, -(-len(items) // NAME_PAGE))
    page = max(0, min(page, pages - 1))
    chunk = items[page * NAME_PAGE:(page + 1) * NAME_PAGE]

    def label(r: str, lbl: str) -> str:
        shown = st.name_for(r) or lbl
        if mode == "p":
            return f"💰 {shown} · {money(st.price_for(r))}" if st.has_custom_price(r) else f"📞 {shown}"
        return f"🏷 {st.name_for(r)}" if st.has_custom_name(r) else f"📞 {lbl}"

    done = st.has_custom_price if mode == "p" else st.has_custom_name
    rows = [[btn(label(r, lbl)[:60], callback_data=f"adm:{mode}rng:{key}:{sv}:{page * NAME_PAGE + i}",
                 style=("primary" if done(r) else "success"))]
            for i, (r, lbl) in enumerate(chunk)]
    _nav(rows, page, pages, lambda pg: f"adm:{mode}pg:{key}:{sv}:{pg}")
    rows.append([btn("⬅️ Back to services", callback_data=f"adm:{mode}sp:{key}:{sv // NAME_PAGE}")])
    srv = dict(name_servers(context)).get(key, key)
    if mode == "p":
        text = (f"💰 <b>Range prices</b> · {html.escape(srv)} · {service_emoji(sid)} {html.escape(sid)}\n\n"
                f"{len(items)} ranges · page {page + 1}/{pages}\n"
                f"💰 = has its own price (others pay {money(st.default_price)}). Tap a range to set its price 👇")
    else:
        text = (f"🏷 <b>Range names</b> · {html.escape(srv)} · {service_emoji(sid)} {html.escape(sid)}\n\n"
                f"{len(items)} ranges · page {page + 1}/{pages}\n"
                "🏷 = already renamed. Tap a range to set its name 👇")
    return text, InlineKeyboardMarkup(rows)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id) or "withdrawals" not in context.application.bot_data:
        await update.effective_message.reply_text("⛔ Admins only.")
        return
    text, kb = admin_home_view(context)
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


def adm_back(cb: str = "adm:home", label: str = "⬅️ Back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn(label, callback_data=cb)]])


def adm_list_kb(rows: list[dict], back: str = "adm:home") -> InlineKeyboardMarkup:
    kb = [[btn(f"#{r['id']} · {money(r['amount'])} · {r['method']} · {(r.get('full_name') or r['user_id'])}"[:60],
               callback_data=f"adm:view:{r['id']}")] for r in rows]
    return InlineKeyboardMarkup(kb + [[btn("⬅️ Back", callback_data=back)]])


async def on_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    uid = q.from_user.id
    if not is_admin(uid) or "withdrawals" not in context.application.bot_data:
        await q.answer("⛔ Admins only", show_alert=True)
        return
    await q.answer()
    parts = q.data.split(":")
    act = parts[1] if len(parts) > 1 else "home"
    arg = parts[2] if len(parts) > 2 else ""
    wd = wd_of(context)
    app = context.application

    if act != "rej":
        context.user_data.pop("adm_reject", None)
    if act not in ("pset", "pdef", "prng"):
        context.user_data.pop("adm_price", None)
    if act != "nrng":
        context.user_data.pop("adm_name", None)

    if act == "home":
        text, kb = admin_home_view(context)
        await safe_edit(q, text, kb)

    elif act == "pend":
        rows = wd.list(PENDING, newest_first=False, limit=20)
        text = f"📥 <b>Pending withdrawals ({len(wd.list(PENDING))})</b>\n\n" + \
               ("Tap one to review it." if rows else "Nothing waiting 🎉")
        await safe_edit(q, text, adm_list_kb(rows))

    elif act == "hist":
        status = arg if arg in (PAID, REJECTED) else PAID
        rows = wd.list(status, limit=15)
        await safe_edit(q, f"{STATUS_LABEL[status]} <b>withdrawals</b> (latest {len(rows)})",
                        adm_list_kb(rows))

    elif act == "view":
        r = wd.get(int(arg))
        if not r:
            await safe_edit(q, "Request not found.", adm_back("adm:pend"))
            return
        rows = []
        if r["status"] == PENDING:
            rows.append([btn("✅ Mark paid", callback_data=f"adm:pay:{r['id']}", style="success"),
                         btn("❌ Reject", callback_data=f"adm:rej:{r['id']}", style="danger")])
        rows.append([btn("⬅️ Back", callback_data="adm:pend" if r["status"] == PENDING else f"adm:hist:{r['status']}")])
        await safe_edit(q, req_text(context, r, True), InlineKeyboardMarkup(rows))

    elif act == "pay":
        r = wd.get(int(arg))
        if not r or r["status"] != PENDING:
            await safe_edit(q, "This request is no longer pending.", adm_back("adm:pend"))
            return
        await safe_edit(q, f"Did you send <b>{money(r['amount'])}</b> to {html.escape(r['method'])} "
                           f"<code>{html.escape(r['account'])}</code>?\n\nThis can't be undone.",
                        InlineKeyboardMarkup([[btn("✅ Yes, paid", callback_data=f"adm:payok:{r['id']}", style="success")],
                                              [btn("⬅️ Back", callback_data=f"adm:view:{r['id']}")]]))

    elif act == "payok":
        r, err = await apply_decision(app, uid, int(arg), PAID)
        await safe_edit(q, f"⚠️ {html.escape(err)}" if err else req_text(context, r, True) + "\n\n✅ User notified.",
                        adm_back("adm:pend", "📥 Pending"))

    elif act == "rej":
        r = wd.get(int(arg))
        if not r or r["status"] != PENDING:
            await safe_edit(q, "This request is no longer pending.", adm_back("adm:pend"))
            return
        context.user_data["adm_reject"] = r["id"]
        await safe_edit(q, f"❌ <b>Reject #{r['id']}</b> ({money(r['amount'])})\n\nType the reason as a message "
                           "(the user will see it), or reject without one. The money goes back to the user.",
                        InlineKeyboardMarkup([[btn("❌ Reject without reason", callback_data=f"adm:rejok:{r['id']}", style="danger")],
                                              [btn("⬅️ Back", callback_data=f"adm:view:{r['id']}")]]))

    elif act == "rejok":
        r, err = await apply_decision(app, uid, int(arg), REJECTED, "")
        await safe_edit(q, f"⚠️ {html.escape(err)}" if err else req_text(context, r, True) + "\n\n❌ User notified, money returned.",
                        adm_back("adm:pend", "📥 Pending"))

    elif act in ("num", "numset"):
        st = settings_of(context)
        if act == "numset" and arg.isdigit():
            st.set_numbers_per_range(int(arg))
        cur = st.numbers_per_range
        nums = [btn((f"✅ {n}" if n == cur else str(n)), callback_data=f"adm:numset:{n}",
                    style=("success" if n == cur else "primary")) for n in range(1, MAX_NUMBERS + 1)]
        rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
        rows.append([btn("⬅️ Back", callback_data="adm:home")])
        await safe_edit(q, "🔢 <b>Numbers per range</b>\n\n"
                           f"Current: <b>{cur}</b>\n\n"
                           "When a user picks a range, the bot gives this many numbers at once. "
                           "Every SMS received on any of them adds the reward of that range (see 💰 Range prices).\n\n"
                           "Tap a number to change it (applies to the next order):",
                        InlineKeyboardMarkup(rows))

    elif act == "price":
        text, kb = price_home_view(context)
        await safe_edit(q, text, kb)

    elif act == "pset":
        context.user_data["adm_price"] = "range"
        await safe_edit(q, "➕ <b>Set a range price</b>\n\n"
                           "Send the range and the reward in taka, one range per line:\n\n"
                           "<code>22501XXX 0.50</code>\n<code>8801XXX 0.25</code>\n\n"
                           "You can also put several ranges on one line with the same price:\n"
                           "<code>22501 22502 22503 0.40</code>\n\n"
                           "(XXX at the end is optional. Max 2 decimals. Use 0 to pay nothing for a range.)",
                        adm_back("adm:price"))

    elif act == "pdef":
        context.user_data["adm_price"] = "default"
        await safe_edit(q, "💵 <b>Default reward</b>\n\n"
                           f"Now: <b>{money(settings_of(context).default_price)}</b> per SMS.\n"
                           "This is paid for every range that has no price of its own.\n\n"
                           "Send the new amount in taka, e.g. <code>0.30</code>",
                        adm_back("adm:price"))

    elif act in ("plist", "pdel"):
        st = settings_of(context)
        if act == "pdel" and arg:
            st.remove_range_price(arg)
        prices = sorted(st.range_prices.items())
        if not prices:
            await safe_edit(q, "📋 No custom range prices. Every range pays "
                               f"<b>{money(st.default_price)}</b>.", adm_back("adm:price"))
            return
        rows = [[btn(f"🗑 {k} · {money(v)}", callback_data=f"adm:pdel:{k}"[:64], style="danger")] for k, v in prices[:40]]
        rows.append([btn("⬅️ Back", callback_data="adm:price")])
        await safe_edit(q, f"📋 <b>Custom range prices ({len(prices)})</b>\n\n"
                           f"Everything else pays the default {money(st.default_price)}.\n"
                           "Tap a range to remove its custom price:", InlineKeyboardMarkup(rows))

    elif act == "name":
        text, kb = name_home_view(context)
        await safe_edit(q, text, kb)

    elif act in ("nsrv", "psrv", "nsp", "psp", "nsv", "psv", "npg", "ppg"):
        mode, step = act[0], act[1:]
        key = arg
        if key not in {k for k, _ in name_servers(context)}:
            text, kb = name_home_view(context)
            await safe_edit(q, text, kb)
            return
        num = lambda i: int(parts[i]) if len(parts) > i and parts[i].isdigit() else 0
        if step == "srv" or key not in context.user_data.get("adm_svc", {}):
            await safe_edit(q, "⏳ Loading services…")
            try:
                services = await providers_of(app)[key].get_services()
            except ApiError as e:
                await show_api_error(q, e, retry=f"adm:{mode}srv:{key}")
                return
            context.user_data.setdefault("adm_svc", {})[key] = [
                (svc.sid, [(r, lbl) for r, lbl in zip(svc.ranges, svc.labels or svc.ranges)])
                for svc in services if svc.ranges]
        if not _svc_cache(context, key):
            await safe_edit(q, "📭 No ranges on this server right now.", adm_back("adm:price" if mode == "p" else "adm:name"))
            return
        if step in ("srv", "sp"):
            text, kb = admin_services_view(context, key, num(3) if step == "sp" else 0, mode)
        else:
            sv = num(3)
            if sv >= len(_svc_cache(context, key)):
                text, kb = admin_services_view(context, key, 0, mode)
            else:
                text, kb = name_ranges_view(context, key, sv, num(4) if step == "pg" else 0, mode)
        await safe_edit(q, text, kb)

    elif act in ("nrng", "prng"):
        mode = act[0]
        key = arg
        sv, idx = (int(parts[i]) if len(parts) > i and parts[i].isdigit() else -1 for i in (3, 4))
        svcs = _svc_cache(context, key)
        if not (0 <= sv < len(svcs) and 0 <= idx < len(svcs[sv][1])):
            text, kb = name_home_view(context) if mode == "n" else price_servers_view(context)
            await safe_edit(q, "⌛ That list is out of date, pick again.\n\n" + text, kb)
            return
        rng = svcs[sv][1][idx][0]
        page = idx // NAME_PAGE
        st = settings_of(context)
        sel = {"range": rng, "key": key, "sv": sv, "page": page}
        rows = []
        if mode == "n":
            context.user_data["adm_name"] = sel
            cur = st.name_for(rng)
            if cur:
                rows.append([btn("🗑 Remove name", callback_data=f"adm:nrm:{key}:{sv}:{idx}", style="danger")])
            rows.append([btn("⬅️ Back", callback_data=f"adm:npg:{key}:{sv}:{page}")])
            await safe_edit(q, f"✏️ <b>Rename range</b> <code>{html.escape(rng)}</code>\n\n"
                               f"Now: <b>{html.escape(cur) if cur else 'no custom name'}</b>\n\n"
                               f"Send the new name as a message (max {MAX_NAME_LEN} characters).",
                            InlineKeyboardMarkup(rows))
        else:
            context.user_data["adm_price"] = sel
            if st.has_custom_price(rng):
                rows.append([btn("🗑 Remove custom price", callback_data=f"adm:prm:{key}:{sv}:{idx}", style="danger")])
            rows.append([btn("⬅️ Back", callback_data=f"adm:ppg:{key}:{sv}:{page}")])
            nm = st.name_for(rng)
            await safe_edit(q, f"💰 <b>Price for range</b> <code>{html.escape(rng)}</code>"
                               + (f" ({html.escape(nm)})" if nm else "") + "\n\n"
                               f"Now: <b>{money(st.price_for(rng))}</b> per SMS"
                               + ("" if st.has_custom_price(rng) else " (default)") + "\n\n"
                               "Send the new reward in taka, e.g. <code>0.50</code> (0 = pays nothing).",
                            InlineKeyboardMarkup(rows))

    elif act == "psel":
        text, kb = price_servers_view(context)
        await safe_edit(q, text, kb)

    elif act in ("nrm", "prm"):
        mode = act[0]
        key = arg
        sv, idx = (int(parts[i]) if len(parts) > i and parts[i].isdigit() else -1 for i in (3, 4))
        svcs = _svc_cache(context, key)
        if 0 <= sv < len(svcs) and 0 <= idx < len(svcs[sv][1]):
            r = svcs[sv][1][idx][0]
            if mode == "p":
                settings_of(context).remove_range_price(r)
                context.user_data.pop("adm_price", None)
            else:
                settings_of(context).remove_range_name(r)
                context.user_data.pop("adm_name", None)
            text, kb = name_ranges_view(context, key, sv, idx // NAME_PAGE, mode)
            await safe_edit(q, ("🗑 Custom price removed.\n\n" if mode == "p" else "🗑 Name removed.\n\n") + text, kb)
        else:
            text, kb = name_home_view(context) if mode == "n" else price_servers_view(context)
            await safe_edit(q, text, kb)

    elif act in ("nlist", "ndel"):
        st = settings_of(context)
        if act == "ndel" and arg:
            st.remove_range_name(arg)
        names = sorted(st.range_names.items())
        if not names:
            await safe_edit(q, "📋 No custom range names. Users see the normal range text.", adm_back("adm:name"))
            return
        rows = [[btn(f"🗑 {k} · {v}"[:60], callback_data=f"adm:ndel:{k}"[:64], style="danger")] for k, v in names[:40]]
        rows.append([btn("⬅️ Back", callback_data="adm:name")])
        await safe_edit(q, f"📋 <b>Named ranges ({len(names)})</b>\n\n"
                           "Tap a range to remove its custom name:", InlineKeyboardMarkup(rows))

    elif act == "stop":
        if settings_of(context).maintenance:
            await safe_edit(q, "🔴 The bot is already stopped.", adm_back("adm:home"))
            return
        await safe_edit(q, "🛑 <b>Stop the bot?</b>\n\n"
                           "• Users get no replies; they only see the <i>update in progress</i> notice.\n"
                           "• Every user receives that notice now.\n"
                           "• You (admin) can still use the bot, and press ▶️ Start when you're done.\n"
                           "• The stop survives a restart of the host.",
                        InlineKeyboardMarkup([[btn("🛑 Yes, stop the bot", callback_data="adm:stopok", style="danger")],
                                              [btn("⬅️ Cancel", callback_data="adm:home")]]))

    elif act == "stopok":
        if not settings_of(context).maintenance:
            settings_of(context).set_maintenance(True)
            app.create_task(broadcast(app, MAINT_TEXT, q.message.chat_id))
        text, kb = admin_home_view(context)
        await safe_edit(q, "🛑 <b>Bot stopped.</b> Sending the update notice to all users…\n\n" + text, kb)

    elif act == "start":
        if settings_of(context).maintenance:
            settings_of(context).set_maintenance(False)
            app.create_task(broadcast(app, BACK_TEXT, q.message.chat_id, with_menu=True))
            note = "▶️ <b>Bot started.</b> Telling the users it is back online…"
        else:
            note = "🟢 The bot is already running."
        text, kb = admin_home_view(context)
        await safe_edit(q, note + "\n\n" + text, kb)

    elif act == "backup":
        await safe_edit(q, "⏳ Sending backup…")
        ok, msg = await app.bot_data["backup"].send_backup(force=True)
        text, kb = admin_home_view(context)
        await safe_edit(q, ("✅ " if ok else "⚠️ ") + html.escape(msg) + "\n\n" + text, kb)

    elif act in ("rsok", "rsno"):
        data = app.bot_data.get("pending_restore", {}).pop(uid, None)
        if act == "rsno" or not data:
            await safe_edit(q, "Restore cancelled." if act == "rsno" else "⌛ That restore request expired. Send the zip again.",
                            adm_back("adm:home", "👑 Admin panel"))
            return
        try:
            res = restore(store_of(context), wd, data)
        except BackupError as e:
            await safe_edit(q, f"⚠️ {html.escape(str(e))}", adm_back("adm:home", "👑 Admin panel"))
            return
        backup_soon(app)
        await safe_edit(q, f"♻️ <b>Restore finished</b>\n\nUsers: {res['users_restored']} of {res['users_in_backup']} updated\n"
                           f"Requests: {res['requests_restored']} of {res['requests_in_backup']} updated\n\n"
                           "(Records that were already newer on this server were kept.)",
                        adm_back("adm:home", "👑 Admin panel"))


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """An admin sends a backup .zip to the bot -> offer to restore it."""
    user, msg = update.effective_user, update.effective_message
    if not is_admin(user.id) or "withdrawals" not in context.application.bot_data:
        return
    doc = msg.document
    if not (doc.file_name or "").lower().endswith(".zip"):
        return
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await msg.reply_text("⚠️ That file is too big for a backup.")
        return
    try:
        f = await doc.get_file()
        data = bytes(await f.download_as_bytearray())
        users, reqs, _, meta = read_backup(data)
    except (BackupError, TelegramError) as e:
        await msg.reply_text(f"⚠️ {html.escape(str(e))}")
        return
    context.application.bot_data.setdefault("pending_restore", {})[user.id] = data
    await msg.reply_text(
        f"📦 <b>Backup found</b>\n\n👥 {len(users)} users · 💸 {len(reqs)} withdrawal requests\n"
        f"🕒 {fmt_utc(str(meta.get('created_at', '')))}\n\nRestore it? It is merged with the current data: for each user "
        "the newer record wins, nothing is deleted.",
        reply_markup=InlineKeyboardMarkup([[btn("♻️ Restore", callback_data="adm:rsok", style="success"),
                                            btn("✖ Cancel", callback_data="adm:rsno", style="danger")]]),
        parse_mode=ParseMode.HTML)


# ----------------------------------------------------------------------------
# App wiring
# ----------------------------------------------------------------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.callback_query:
        try:
            await update.callback_query.message.reply_text(
                "Something went wrong. Please try again.", reply_markup=main_menu_kb()
            )
        except Exception:
            pass


async def post_init(app: Application) -> None:
    app.bot_data["store"] = UserStore()
    app.bot_data["settings"] = SettingsStore()
    await app.bot_data["store"].start()
    wd = WithdrawStore(app.bot_data["store"])
    try:
        wd.load()
        FEATURES["withdraw"] = bool(ADMIN_IDS)
    except StorageError as e:
        log.error("Withdrawals are OFF: %s", e)
    app.bot_data["withdrawals"] = wd
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS not set - withdrawals and the admin panel are OFF.")
    backups = BackupManager(app.bot, app.bot_data["store"], wd, sorted(ADMIN_IDS))
    app.bot_data["backup"] = backups
    await backups.start()                       # merges the pinned backup (if any) before anything is loaded
    log.info("Backups: %s%s", backups.mode, f" ({backups.startup_note})" if backups.startup_note else "")
    if not (backups.chat or ADMIN_IDS):
        log.warning("No BACKUP_CHAT_ID / ADMIN_IDS - data exists ONLY on this server's disk.")
    if app.bot_data["settings"].maintenance:
        log.warning("Bot is in STOPPED mode (maintenance) - users only see the update notice. Admin panel -> Start.")
    app.bot_data["migration"] = asyncio.create_task(app.bot_data["store"].migrate_all())
    app.bot_data["providers"] = build_providers()
    enabled_keys = [k for k, p in app.bot_data["providers"].items() if p.enabled]
    MENU_PROVIDERS[:] = [k for k in GET_NUMBER_ORDER if k in enabled_keys] + [k for k in enabled_keys if k not in GET_NUMBER_ORDER]
    on = [p.label for p in app.bot_data["providers"].values() if p.enabled]
    off = [f"{p.label} ({p.api_key_env})" for p in app.bot_data["providers"].values() if not p.enabled]
    if on:
        log.info("SMS servers enabled: %s", ", ".join(on))
    else:
        log.error("No SMS server has an API key. Set VOLTXSMS_API_KEY and/or ZENEX_API_KEY.")
    if off:
        log.warning("SMS servers disabled (no key): %s", ", ".join(off))
    console = ConsoleClient(app.bot_data["providers"])
    app.bot_data["console"] = console
    CONSOLE_KEYS[:] = [k for k in GET_NUMBER_ORDER if console.available(k)]
    log.info("Consoles available: %s", ", ".join(f"Console {site_number(k)} ({k})" for k in CONSOLE_KEYS) or "none")
    TOP_KEYS[:] = [k for k in CONSOLE_KEYS if k in enabled_keys]
    if TOP_KEYS:
        tracker = TrafficTracker(console.fetch, lambda: list(TOP_KEYS))
        app.bot_data["traffic"] = tracker
        await tracker.start()
        log.info("Top Traffic on for: %s (last %d min)", ", ".join(TOP_KEYS), tracker.window_min)
    else:
        log.warning("Top Traffic is OFF (no console + API key available).")
    feed = ChannelFeed(app.bot, console.fetch, lambda: list(CONSOLE_KEYS), site_number)
    if feed.enabled:
        app.bot_data["feed"] = feed
        await feed.start()
    else:
        log.warning("CHANNEL_ID not set - channel SMS feed is OFF.")
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "Open the menu"),
            BotCommand("balance", "Show my balance"),
            BotCommand("console", "Open the site consoles"),
            BotCommand("support", "Contact support"),
            BotCommand("withdraw", "Withdraw your balance"),
            BotCommand("help", "How it works"),
        ])
    except TelegramError as e:
        log.warning("Could not set the command menu: %s", e)


async def post_shutdown(app: Application) -> None:
    feed = app.bot_data.get("feed")
    if feed:
        await feed.stop()
    traffic = app.bot_data.get("traffic")
    if traffic:
        await traffic.stop()
    if app.bot_data.get("backup"):
        await app.bot_data["backup"].stop()
    if app.bot_data.get("console"):
        await app.bot_data["console"].close()
    for act in list(ACTIVATIONS.values()):
        stop_polling(act)
    for prov in app.bot_data.get("providers", {}).values():
        await prov.close()
    store = app.bot_data.get("store")
    if store:
        await store.close()


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (from @BotFather) before starting.")

    app = (ApplicationBuilder().token(token)
           .post_init(post_init).post_shutdown(post_shutdown).build())

    app.add_handler(TypeHandler(Update, maintenance_gate), group=-1)     # Stop mode: blocks everyone except admins
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("balance", msg_balance))
    app.add_handler(CommandHandler("help", msg_help))
    app.add_handler(CommandHandler("support", msg_support))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_SUPPORT}$"), msg_support))
    app.add_handler(MessageHandler(filters.Regex(r"^📲 Get Number( \d)?$"), msg_get_number))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_BAL}$"), msg_balance))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_HELP}$"), msg_help))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_CONSOLE}$"), msg_console))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_TOP}$"), msg_top_traffic))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_WITHDRAW}$"), msg_withdraw))
    app.add_handler(CommandHandler("withdraw", msg_withdraw))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_ADMIN}$"), cmd_admin))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=1)
    app.add_handler(CommandHandler("console", msg_console))
    for handler, pattern in [
        (on_menu, r"^menu$"), (on_help, r"^help$"), (on_balance, r"^balance$"),
        (on_get_number, r"^get_number$"), (on_prov, r"^prov:[a-z0-9_]+$"), (on_back_services, r"^back_services$"),
        (on_svc_page, r"^svcpage:\d+$"), (on_svc, r"^svc:\d+$"),
        (on_rng_page, r"^rngpage:\d+$"), (on_rng, r"^rng:\d+$"),
        (on_change, r"^change$"), (on_console, r"^console:[a-z0-9_]+$"),
        (on_top_traffic, r"^toptraffic$"), (on_top_list, r"^toplist:[a-z0-9_]+$"),
        (on_top_go, r"^topgo:[a-z0-9_]+:\d+$"),
        (on_withdraw, r"^withdraw$"), (on_wd, r"^wd:[a-z]+$"), (on_wd_method, r"^wdm:\d+$"),
        (on_wd_cancel_req, r"^wdc:\d+$"), (on_admin, r"^adm:"),
    ]:
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))
    app.add_error_handler(on_error)

    log.info("Bot starting…")
    app.run_polling()


if __name__ == "__main__":
    main()
