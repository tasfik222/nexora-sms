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
    CHANNEL_ID                  channel (-100... or @name) that receives every new SMS from both sites
                                (bot must be channel admin) - see channel_feed.py
    GITHUB_TOKEN, GITHUB_REPO   (optional) where per-user data is synced - see storage.py

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
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

from telegram import InlineKeyboardButton as Btn
from telegram import BotCommand, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram import KeyboardButton as KBtn
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
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
from storage import StorageError, UserStore

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
PRICE_PER_CODE = 30     # paisa added per SMS received  ->  30 paisa = ৳0.30
CURRENCY = "৳"


def money(paisa: int) -> str:
    """30 -> '৳0.30', 150 -> '৳1.50', 12345 -> '৳123.45'"""
    return f"{CURRENCY}{paisa // 100}.{paisa % 100:02d}"

POLL_INTERVAL = 5       # seconds between automatic checks (/success-otp is cached ~5s)
POLL_TIMEOUT = 1200     # keep waiting up to 20 minutes
CLOCK_SKEW_MS = 30_000  # only used if the "already seen" snapshot failed
DOUBLE_TAP_MS = 4_000   # ignore a 2nd order tap within this time of the 1st


# ----------------------------------------------------------------------------
# In-memory state: one active activation per Telegram user. No database.
# ----------------------------------------------------------------------------
@dataclass
class Activation:
    phone: str
    no_plus: str
    country: str
    operator: str
    rid: str                 # range exactly as listed by the provider, e.g. "22501XXX"
    provider: str            # provider key: "voltx" / "zenex"
    chat_id: int
    message_id: int
    order_ms: int
    seen_ids: set = field(default_factory=set)
    seen_ok: bool = False
    done: bool = False
    message: str = ""
    code: Optional[str] = None
    last_checked: Optional[str] = None
    credit_line: str = ""
    poll_task: Optional[asyncio.Task] = field(default=None, repr=False)


ACTIVATIONS: dict[int, Activation] = {}
LOCKS: dict[int, asyncio.Lock] = {}
NUMBER_OWNER: dict[str, int] = {}  # number -> the ONE Telegram user it was allocated to


def user_lock(uid: int) -> asyncio.Lock:
    return LOCKS.setdefault(uid, asyncio.Lock())


def store_of(context: ContextTypes.DEFAULT_TYPE) -> UserStore:
    return context.application.bot_data["store"]


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


def console_buttons() -> list:
    """[Console 1, Console 2] - click shows that site's live console inside Telegram."""
    return [btn(f"🖥 Console {site_number(k)}", callback_data=f"console:{k}",
                style=("success", "primary")[(site_number(k) - 1) % 2]) for k in CONSOLE_KEYS]


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


def bottom_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[kbtn(lbl, ("success", "primary")[i % 2]) for i, (_, lbl) in enumerate(get_buttons())],
         [kbtn(BTN_BAL, "primary")] + ([kbtn(BTN_CONSOLE, "success")] if console_buttons() else []) + [kbtn(BTN_HELP, "primary")]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="Choose from the menu below 👇",
    )


def main_menu_kb() -> InlineKeyboardMarkup:
    rows = [inline_get_row()]
    if console_buttons():
        rows.append(console_buttons())
    return InlineKeyboardMarkup(rows + [
        [btn(BTN_BAL, callback_data="balance", style="primary")],
        [btn(BTN_HELP, callback_data="help", style="primary")],
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


def activation_kb(act: Activation) -> InlineKeyboardMarkup:
    if act.done:
        return InlineKeyboardMarkup([
            [btn("🔄 Change number", callback_data="change", style="primary")],
            [btn("📲 Pick another range", callback_data=f"prov:{act.provider}", style="success")],
            [btn("🏠 Main menu", callback_data="menu")],
        ])
    return InlineKeyboardMarkup([[btn("🔄 Change number", callback_data="change", style="danger")]])


def activation_text(act: Activation) -> str:
    phone = html.escape(act.phone)
    info = " · ".join(x for x in (act.country, act.operator) if x)
    info_line = f"\n{html.escape(info)}" if info else ""
    if act.done:
        code_line = f"\nCode: <code>{html.escape(act.code)}</code>" if act.code else ""
        return (f"✅ <b>SMS received</b>\n\nNumber: <code>{phone}</code>{info_line}{code_line}\n\n"
                f"Message:\n<i>{html.escape(act.message)}</i>{act.credit_line}")
    return (f"📱 <b>Your number</b>\n\n<code>{phone}</code>{info_line}\n\n"
            "⏳ Waiting for SMS (up to 20 minutes). It will show up here automatically.")


HELP_TEXT = (
    "<b>How it works</b>\n\n"
    "1. Tap a <b>Get Number</b> button, then pick a service and a range.\n"
    "2. Use the number on the app you're verifying.\n"
    "3. The bot checks for the SMS every 5 seconds for up to 20 minutes and sends it here automatically.\n"
    "4. Tap <b>Change number</b> to get a different number from the same range.\n"
    f"5. Every SMS you receive adds {money(PRICE_PER_CODE)} to your balance (see <b>My Balance</b>)."
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
        key = owner_key(act.provider, act.no_plus)
        if NUMBER_OWNER.get(key) == uid:
            NUMBER_OWNER.pop(key, None)


def fmt_time(ms: int) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ms / 1000)) if ms else "?"


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
        reply_markup=bottom_menu_kb(), parse_mode=ParseMode.HTML,
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
    labels = [f"📞 {x}" for x in (svc.labels or svc.ranges)]
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

        await safe_edit(q, f"⏳ Getting a number from <code>{html.escape(rid)}</code>…")
        order_ms = int(time.time() * 1000)
        try:
            order = await api.get_number(rid)
        except ApiError as e:
            await show_api_error(q, e, retry=retry)
            return

        # Snapshot OTPs already on record for this number so only NEW ones count.
        seen_ids, seen_ok = set(), False
        try:
            seen_ids = {o.otp_id for o in await api.get_otps() if o.number == order.no_plus_number}
            seen_ok = True
        except ApiError as e:
            log.warning("Could not snapshot existing OTPs: %s", e)

        clear_activation(uid)
        act = Activation(
            phone=order.full_number, no_plus=order.no_plus_number, country=order.country,
            operator=order.operator, rid=rid, provider=provider, chat_id=q.message.chat_id,
            message_id=q.message.message_id, order_ms=order_ms, seen_ids=seen_ids, seen_ok=seen_ok,
        )
        ACTIVATIONS[uid] = act
        NUMBER_OWNER[owner_key(provider, order.no_plus_number)] = uid
        act.poll_task = context.application.create_task(poll_for_sms(context.application, uid))

    await safe_edit(q, activation_text(act), activation_kb(act))


# ----------------------------------------------------------------------------
# SMS delivery: background polling + manual check
# ----------------------------------------------------------------------------
def find_new_otp(otps: list[Otp], act: Activation) -> Optional[Otp]:
    fresh = []
    for o in otps:
        if o.number != act.no_plus:
            continue
        if act.seen_ok:
            if o.otp_id in act.seen_ids:
                continue
        elif o.time and o.time < act.order_ms - CLOCK_SKEW_MS:
            continue
        fresh.append(o)
    return max(fresh, key=lambda o: o.time) if fresh else None


async def deliver_otp(app: Application, uid: int, otp: Otp) -> None:
    act = ACTIVATIONS.get(uid)
    if not act or act.done:
        return
    if NUMBER_OWNER.get(owner_key(act.provider, act.no_plus)) != uid:  # number now belongs to someone else
        stop_polling(act)
        return
    act.done = True
    act.message = otp.message
    act.code = extract_code(otp.message)
    new_balance = app.bot_data["store"].credit(uid, PRICE_PER_CODE, act.phone, act.code, f"{act.provider}:{otp.otp_id}")
    if new_balance is not None:
        act.credit_line = f"\n\n💰 <b>+{money(PRICE_PER_CODE)}</b> added. Balance: {money(new_balance)}"
    stop_polling(act)
    try:
        await app.bot.edit_message_text(
            activation_text(act), chat_id=act.chat_id, message_id=act.message_id,
            reply_markup=activation_kb(act), parse_mode=ParseMode.HTML,
        )
    except BadRequest as e:
        log.debug("Could not edit panel: %s", e)
    head = f"🔔 SMS for <code>{html.escape(act.phone)}</code>"
    body = f"\nCode: <code>{html.escape(act.code)}</code>" if act.code else ""
    await app.bot.send_message(
        act.chat_id, f"{head}{body}\n<i>{html.escape(otp.message)}</i>{act.credit_line}", parse_mode=ParseMode.HTML
    )


async def poll_for_sms(app: Application, uid: int) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT
    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            act = ACTIVATIONS.get(uid)
            if not act or act.done or NUMBER_OWNER.get(owner_key(act.provider, act.no_plus)) != uid:
                return
            try:
                otp = find_new_otp(await providers_of(app)[act.provider].get_otps(), act)
            except ApiKeyError as e:
                log.error("Polling stopped, API key problem: %s", e)
                await app.bot.send_message(act.chat_id, f"⚠️ {e.user_message}")
                return
            except ApiError as e:
                log.warning("Poll error (will retry): %s", e)
                continue
            if otp:
                await deliver_otp(app, uid, otp)
                return

        act = ACTIVATIONS.get(uid)
        if act and not act.done:
            await app.bot.send_message(
                act.chat_id,
                "⌛ No SMS after 20 minutes. Tap <b>Change number</b> to get a new number.",
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
        await update.effective_message.reply_text("🔄 Menu updated 👇", reply_markup=bottom_menu_kb())
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


async def msg_console(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buttons = console_buttons()
    if not buttons:
        await update.effective_message.reply_text(
            "⚠️ No SMS server is configured yet. Please contact the bot admin.", reply_markup=bottom_menu_kb())
        return
    await update.effective_message.reply_text(
        "🖥 <b>Choose a console</b>", reply_markup=InlineKeyboardMarkup([buttons]), parse_mode=ParseMode.HTML)


async def msg_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=bottom_menu_kb())


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
    await app.bot_data["store"].start()
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
            BotCommand("help", "How it works"),
        ])
    except TelegramError as e:
        log.warning("Could not set the command menu: %s", e)


async def post_shutdown(app: Application) -> None:
    feed = app.bot_data.get("feed")
    if feed:
        await feed.stop()
    if app.bot_data.get("console"):
        await app.bot_data["console"].close()
    for act in list(ACTIVATIONS.values()):
        stop_polling(act)
    for prov in app.bot_data.get("providers", {}).values():
        await prov.close()
    store = app.bot_data.get("store")
    if store:
        await store.close()  # final push of unsaved changes to GitHub


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (from @BotFather) before starting.")

    app = (ApplicationBuilder().token(token)
           .post_init(post_init).post_shutdown(post_shutdown).build())

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("balance", msg_balance))
    app.add_handler(CommandHandler("help", msg_help))
    app.add_handler(MessageHandler(filters.Regex(r"^📲 Get Number( \d)?$"), msg_get_number))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_BAL}$"), msg_balance))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_HELP}$"), msg_help))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_CONSOLE}$"), msg_console))
    app.add_handler(CommandHandler("console", msg_console))
    for handler, pattern in [
        (on_menu, r"^menu$"), (on_help, r"^help$"), (on_balance, r"^balance$"),
        (on_get_number, r"^get_number$"), (on_prov, r"^prov:[a-z0-9_]+$"), (on_back_services, r"^back_services$"),
        (on_svc_page, r"^svcpage:\d+$"), (on_svc, r"^svc:\d+$"),
        (on_rng_page, r"^rngpage:\d+$"), (on_rng, r"^rng:\d+$"),
        (on_change, r"^change$"), (on_console, r"^console:[a-z0-9_]+$"),
    ]:
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))
    app.add_error_handler(on_error)

    log.info("Bot starting…")
    app.run_polling()


if __name__ == "__main__":
    main()
