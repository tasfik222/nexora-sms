"""
arena.py - the 🎮 Game button: Arena mini-games inside this bot (ported from the separate aiogram game bot).

Games: Quiz, Scramble, Code Breaker, Tic-Tac-Toe, Memory.  Modes: single player, duo (2), squad (4).
Rules live in arena_games.py; this file is the Telegram side.

Where data lives (nothing new is needed on disk):
  * Arena profile (game coins, XP, level, ELO, stats, daily streak, language) is stored INSIDE each user's
    JSON record under the key "arena". It is separate from the SMS balance: no game reward ever changes
    the taka balance or the withdrawals.
  * Live matches, queues and rooms are kept in memory. They are short-lived, so a restart just ends them.

Fixes compared with the old game bot:
  * rewards are actually paid when a game ends (the old bot never called its reward code);
  * a quiz round that nobody finishes is ended by a 20-second timer, so the match can't stall;
  * winners are shown by name, not by Telegram id;
  * idle matches and old rooms are cleaned up.
"""
from __future__ import annotations

import asyncio
import copy
import html
import json
import logging
import random
import secrets
import string
import time
from dataclasses import dataclass, field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import arena_games as ag
from arena_games import GAMES, GAME_ORDER, ROUND_SECONDS, outcome
from arena_quiz_data import QUIZ_QUESTIONS

log = logging.getLogger("voltx.arena")

IDLE_SECONDS = 600          # a match with no move for this long can be replaced by a new game
ROOM_SECONDS = 1800         # an unused room lobby expires after 30 minutes
MATCH_KEEP = 3600           # finished matches are forgotten after an hour
STARTING_COINS = 100
DEFAULT_ELO = 1000

# ----------------------------------------------------------------------------- strings (English / Bangla)
S = {
    'en': {
        'home': "🎮 <b>Arena</b>\nPlay mini-games, earn XP and game coins, climb the ELO ladder.\n\n"
                "<i>Game coins and XP are separate from your SMS balance. Nothing here changes your taka.</i>",
        'choose_game': "🎮 <b>Choose a game</b>", 'choose_mode': "<b>{game}</b>\nChoose how to play:",
        'ai_level': "🤖 <b>Tic-Tac-Toe vs bot</b>\nChoose the difficulty:",
        'queue_wait': "⏳ <b>Looking for players…</b>\nYou will be matched automatically. Tap Cancel to stop.",
        'queue_cancelled': "🛑 Matchmaking cancelled.",
        'queue_btn': "⚡ Quick match", 'room_btn': "🔑 Private room",
        'cancel': "✖ Cancel", 'back': "⬅️ Back", 'menu': "🏠 Arena menu", 'play_again': "🔁 Play again",
        'play': "🎯 Play", 'start': "▶️ Start game", 'profile': "👤 Profile", 'leaderboard': "🏆 Leaderboard",
        'daily': "🎁 Daily reward", 'language': "🌐 Language", 'help': "❓ How it works",
        'single': "🧍 Single", 'duo': "👥 Duo (2)", 'squad': "👨‍👩‍👧‍👦 Squad (4)",
        'easy': "🟢 Easy", 'medium': "🟡 Medium", 'hard': "🔴 Hard",
        'g_quiz': "🧠 Quiz", 'g_scramble': "🔤 Word Scramble", 'g_codebreaker': "🔢 Code Breaker",
        'g_tictactoe': "❌ Tic-Tac-Toe", 'g_memory': "🃏 Memory",
        'profile_txt': "👤 <b>{name}</b>\nLevel {level} · XP {xp}\n🪙 Game coins {coins}\n⭐ ELO {elo}\n"
                       "Games {played} · Wins {wins}\n🔥 Daily streak {streak}",
        'daily_ok': "🎁 Claimed <b>+{coins} coins</b> and <b>+20 XP</b>. Streak: {streak} day(s).",
        'daily_wait': "🎁 Already claimed. Come back in {h}h {m}m.",
        'lb_title': "🏆 <b>Arena leaderboard (ELO)</b>", 'lb_empty': "No Arena players yet.",
        'lang_set': "🌐 Language set to English.",
        'help_text': "<b>How it works</b>\n1. Pick a game and a mode.\n2. Single = play alone; Duo/Squad = quick match "
                     "or a private room (friends join with <code>/join CODE</code>).\n3. Quiz: 20 seconds per question.\n"
                     "4. Win to gain ELO, XP and game coins. Daily reward every day.",
        'room_created': "🔑 <b>Private room</b> <code>{code}</code>\nFriends join by sending <code>/join {code}</code>.\n"
                        "Players: {count}/{need}. Start when ready.",
        'room_joined_you': "✅ You joined room <code>{code}</code> ({count}/{need}).",
        'room_joined_host': "👤 {name} joined your room ({count}/{need}).",
        'room_not_found': "⚠️ That room does not exist or has already started.",
        'room_full': "⚠️ That room is full.", 'room_already': "You are already in this room.",
        'room_need': "Need {need} players to start (now {count}).",
        'room_host_only': "Only the host can start or cancel this room.",
        'room_cancelled': "🛑 Room cancelled.", 'room_closed_member': "The host closed room {code}.",
        'room_started_out': "The room started without you. Ask the host to start another game.",
        'busy': "You are already in a game. Finish it first.", 'expired': "This game has ended.",
        'not_yours': "This is not your game.", 'not_your_turn': "⏳ Not your turn yet.",
        'already_answered': "You already answered this one.", 'round_passed': "That question is over.",
        'invalid': "That move is not allowed.", 'guess_hint': "Send <code>/guess 1234</code> (4 digits).",
        'guess_wrong_len': "Send exactly 4 digits, e.g. /guess 1234.", 'wrong_word': "❌ Not quite, try again.",
        'quiz_head': "🧠 <b>Quiz</b> · question {n}/{total} · {secs}s",
        'quiz_status': "Answered: {done}/{players}", 'time_up': "⏰ Time is up for that question.",
        'ttt_your_move': "❌ <b>Your move.</b>", 'ttt_their_move': "⏳ {name}'s move.",
        'ttt_bot_vs': "You are X. The bot is O.",
        'mem_head': "🃏 <b>Memory</b> · pick two cards", 'mem_turn': "Turn: {name}",
        'cb_head': "🔢 <b>Code Breaker</b> · guess the 4-digit code", 'cb_turn': "Turn: {name}",
        'cb_empty': "Send <code>/guess 1234</code> to try.",
        'scr_head': "🔤 <b>Word Scramble</b>\nUnscramble: <b>{word}</b>\nType the word in the chat.",
        'res_head': "🏁 <b>Game over</b>", 'res_win': "🏆 <b>You win!</b>", 'res_loss': "💀 You lose.",
        'res_draw': "🤝 Draw.", 'res_bot': "🤖 Bot wins.", 'res_winner': "Winner: <b>{name}</b>",
        'res_scores': "Scores: {scores}", 'res_ended': "⏹ Game ended (no moves for 10 minutes).",
        'rew': "+{xp} XP · +{coins} coins{elo}",
        'you': "You", 'bot': "🤖 Bot", 'nobody': "nobody",
    },
    'bn': {
        'home': "🎮 <b>Arena</b>\nছোট ছোট গেম খেলুন, XP ও গেম কয়েন জিতুন, ELO র‍্যাঙ্কে উঠুন।\n\n"
                "<i>গেম কয়েন ও XP আপনার SMS ব্যালেন্সের আলাদা। এখানে কিছুই আপনার টাকা বদলায় না।</i>",
        'choose_game': "🎮 <b>একটি গেম বেছে নিন</b>", 'choose_mode': "<b>{game}</b>\nকীভাবে খেলবেন?",
        'ai_level': "🤖 <b>বটের সাথে Tic-Tac-Toe</b>\nলেভেল বেছে নিন:",
        'queue_wait': "⏳ <b>খেলোয়াড় খোঁজা হচ্ছে…</b>\nমিল পেলে আপনাকে জানানো হবে। বাতিল করতে Cancel চাপুন।",
        'queue_cancelled': "🛑 খোঁজা বাতিল হয়েছে।",
        'queue_btn': "⚡ দ্রুত ম্যাচ", 'room_btn': "🔑 ব্যক্তিগত রুম",
        'cancel': "✖ বাতিল", 'back': "⬅️ ফিরে যান", 'menu': "🏠 Arena মেনু", 'play_again': "🔁 আবার খেলুন",
        'play': "🎯 খেলুন", 'start': "▶️ খেলা শুরু", 'profile': "👤 প্রোফাইল", 'leaderboard': "🏆 লিডারবোর্ড",
        'daily': "🎁 দৈনিক পুরস্কার", 'language': "🌐 ভাষা", 'help': "❓ কীভাবে খেলবেন",
        'single': "🧍 একা", 'duo': "👥 দুজন", 'squad': "👨‍👩‍👧‍👦 দল (৪ জন)",
        'easy': "🟢 সহজ", 'medium': "🟡 মাঝারি", 'hard': "🔴 কঠিন",
        'g_quiz': "🧠 কুইজ", 'g_scramble': "🔤 শব্দ সাজান", 'g_codebreaker': "🔢 কোড ভাঙুন",
        'g_tictactoe': "❌ টিক-ট্যাক-টো", 'g_memory': "🃏 মেমোরি",
        'profile_txt': "👤 <b>{name}</b>\nলেভেল {level} · XP {xp}\n🪙 গেম কয়েন {coins}\n⭐ ELO {elo}\n"
                       "খেলা {played} · জয় {wins}\n🔥 দৈনিক ধারা {streak}",
        'daily_ok': "🎁 <b>+{coins} কয়েন</b> ও <b>+20 XP</b> পেয়েছেন। ধারা: {streak} দিন।",
        'daily_wait': "🎁 আজকের পুরস্কার নেওয়া হয়ে গেছে। {h} ঘণ্টা {m} মিনিট পর আসুন।",
        'lb_title': "🏆 <b>Arena লিডারবোর্ড (ELO)</b>", 'lb_empty': "এখনো কোনো Arena খেলোয়াড় নেই।",
        'lang_set': "🌐 ভাষা বাংলা করা হয়েছে।",
        'help_text': "<b>কীভাবে খেলবেন</b>\n১. একটি গেম ও মোড বেছে নিন।\n২. একা = একা খেলা; দুজন/দল = দ্রুত ম্যাচ বা "
                     "ব্যক্তিগত রুম (বন্ধুরা <code>/join CODE</code> লিখে যোগ দেবে)।\n৩. কুইজ: প্রতি প্রশ্নে ২০ সেকেন্ড।\n"
                     "৪. জিতলে ELO, XP ও গেম কয়েন। প্রতিদিন দৈনিক পুরস্কার।",
        'room_created': "🔑 <b>ব্যক্তিগত রুম</b> <code>{code}</code>\nবন্ধুরা <code>/join {code}</code> লিখে যোগ দেবে।\n"
                        "খেলোয়াড়: {count}/{need}। প্রস্তুত হলে শুরু করুন।",
        'room_joined_you': "✅ রুম <code>{code}</code>-এ যোগ দিয়েছেন ({count}/{need})।",
        'room_joined_host': "👤 {name} আপনার রুমে যোগ দিয়েছে ({count}/{need})।",
        'room_not_found': "⚠️ এই রুম নেই বা খেলা শুরু হয়ে গেছে।",
        'room_full': "⚠️ এই রুম ভর্তি।", 'room_already': "আপনি আগে থেকেই এই রুমে আছেন।",
        'room_need': "শুরু করতে {need} জন লাগবে (এখন {count} জন)।",
        'room_host_only': "শুধু হোস্ট এই রুম শুরু বা বাতিল করতে পারবে।",
        'room_cancelled': "🛑 রুম বাতিল হয়েছে।", 'room_closed_member': "হোস্ট রুম {code} বন্ধ করেছে।",
        'room_started_out': "রুমের খেলা আপনাকে ছাড়াই শুরু হয়েছে। হোস্টকে আবার শুরু করতে বলুন।",
        'busy': "আপনি আগে থেকেই একটি গেমে আছেন। আগে সেটি শেষ করুন।", 'expired': "এই গেমটি শেষ হয়ে গেছে।",
        'not_yours': "এটি আপনার গেম নয়।", 'not_your_turn': "⏳ এখনো আপনার পালা নয়।",
        'already_answered': "এই প্রশ্নের উত্তর আপনি আগেই দিয়েছেন।", 'round_passed': "এই প্রশ্নের সময় শেষ।",
        'invalid': "এই চাল বৈধ নয়।", 'guess_hint': "<code>/guess 1234</code> লিখুন (৪ সংখ্যা)।",
        'guess_wrong_len': "ঠিক ৪টি সংখ্যা লিখুন, যেমন /guess 1234।", 'wrong_word': "❌ হয়নি, আবার চেষ্টা করুন।",
        'quiz_head': "🧠 <b>কুইজ</b> · প্রশ্ন {n}/{total} · {secs} সেকেন্ড",
        'quiz_status': "উত্তর দিয়েছে: {done}/{players}", 'time_up': "⏰ এই প্রশ্নের সময় শেষ।",
        'ttt_your_move': "❌ <b>আপনার চাল।</b>", 'ttt_their_move': "⏳ {name}-এর চাল।",
        'ttt_bot_vs': "আপনি X, বট O।",
        'mem_head': "🃏 <b>মেমোরি</b> · দুটি কার্ড বেছে নিন", 'mem_turn': "পালা: {name}",
        'cb_head': "🔢 <b>কোড ভাঙুন</b> · ৪ সংখ্যার কোড অনুমান করুন", 'cb_turn': "পালা: {name}",
        'cb_empty': "চেষ্টা করতে <code>/guess 1234</code> লিখুন।",
        'scr_head': "🔤 <b>শব্দ সাজান</b>\nএই শব্দটি সাজান: <b>{word}</b>\nচ্যাটে শব্দটি লিখুন।",
        'res_head': "🏁 <b>খেলা শেষ</b>", 'res_win': "🏆 <b>আপনি জিতেছেন!</b>", 'res_loss': "💀 আপনি হেরেছেন।",
        'res_draw': "🤝 ড্র।", 'res_bot': "🤖 বট জিতেছে।", 'res_winner': "বিজয়ী: <b>{name}</b>",
        'res_scores': "স্কোর: {scores}", 'res_ended': "⏹ খেলা শেষ (১০ মিনিট কোনো চাল ছিল না)।",
        'rew': "+{xp} XP · +{coins} কয়েন{elo}",
        'you': "আপনি", 'bot': "🤖 বট", 'nobody': "কেউ না",
    },
}
GAME_LABEL_KEY = {g: f'g_{g}' for g in GAME_ORDER}


def tr(lang: str, key: str, **kw) -> str:
    table = S.get(lang, S['en'])
    return table.get(key, S['en'].get(key, key)).format(**kw)


# ----------------------------------------------------------------------------- in-memory live state
@dataclass
class Match:
    mid: int
    game: str
    mode: str
    players: list[int]
    state: dict
    names: dict[int, str]
    langs: dict[int, str]
    msgs: dict[int, tuple[int, int]] = field(default_factory=dict)    # uid -> (chat id, message id)
    finished: bool = False
    last: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timer: asyncio.Task | None = None
    ended_at: float = 0.0


@dataclass
class Room:
    code: str
    host: int
    game: str
    mode: str
    need: int
    members: list[int]
    names: dict[int, str]
    created: float = field(default_factory=time.time)


MATCHES: dict[int, Match] = {}
USER_MATCH: dict[int, int] = {}                  # uid -> the match they are in
QUEUES: dict[tuple[str, str], list[dict]] = {}   # (game, mode) -> waiting players
ROOMS: dict[str, Room] = {}
USER_ROOM: dict[int, str] = {}
_ids = iter(range(1, 10**9))


# ----------------------------------------------------------------------------- profile (stored in the user's JSON record)
def profile_of(rec: dict) -> dict:
    p = rec.setdefault("arena", {})
    p.setdefault("lang", "en")
    p.setdefault("elo", DEFAULT_ELO)
    p.setdefault("xp", 0)
    p.setdefault("coins", STARTING_COINS)
    p.setdefault("played", 0)
    p.setdefault("wins", 0)
    p.setdefault("daily_at", 0.0)
    p.setdefault("streak", 0)
    p.setdefault("stats", {})
    return p


def _name_of(rec: dict, uid: int) -> str:
    return (rec.get("first_name") or rec.get("username") or f"Player {str(uid)[-4:]}")[:40]


async def _load(app: Application, tg_user) -> tuple[dict, dict]:
    store = app.bot_data["store"]
    rec = await store.get(tg_user.id, tg_user.username)
    return rec, profile_of(rec)


def _level(xp: int) -> int:
    return xp // 100 + 1


# ----------------------------------------------------------------------------- keyboards
def _b(text: str, cb: str, style: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb, api_kwargs={"style": style} if style else None)


def home_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_b(tr(lang, 'play'), "ar:games", "success")],
        [_b(tr(lang, 'profile'), "ar:prof", "primary"), _b(tr(lang, 'leaderboard'), "ar:lb", "primary")],
        [_b(tr(lang, 'daily'), "ar:daily", "success"), _b(tr(lang, 'language'), "ar:lang", "primary")],
        [_b(tr(lang, 'help'), "ar:help", "primary")],
    ])


def games_markup(lang: str) -> InlineKeyboardMarkup:
    rows = [[_b(tr(lang, GAME_LABEL_KEY[g]), f"ar:game:{g}", "primary")] for g in GAME_ORDER]
    rows.append([_b(tr(lang, 'back'), "ar:home")])
    return InlineKeyboardMarkup(rows)


def modes_markup(game: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_b(tr(lang, 'single'), f"ar:mode:{game}:single", "success"),
         _b(tr(lang, 'duo'), f"ar:mode:{game}:duo", "primary"),
         _b(tr(lang, 'squad'), f"ar:mode:{game}:squad", "primary")],
        [_b(tr(lang, 'back'), "ar:games")],
    ])


def difficulty_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_b(tr(lang, 'easy'), "ar:diff:tictactoe:easy", "success"),
         _b(tr(lang, 'medium'), "ar:diff:tictactoe:medium", "primary"),
         _b(tr(lang, 'hard'), "ar:diff:tictactoe:hard", "danger")],
        [_b(tr(lang, 'back'), "ar:game:tictactoe")],
    ])


def queue_markup(game: str, mode: str, lang: str) -> InlineKeyboardMarkup:
    rows = [[_b(tr(lang, 'queue_btn'), f"ar:queue:{game}:{mode}", "success")]]
    if mode != 'single':
        rows.append([_b(tr(lang, 'room_btn'), f"ar:room:{game}:{mode}", "primary")])
    rows.append([_b(tr(lang, 'back'), f"ar:game:{game}")])
    return InlineKeyboardMarkup(rows)


def cancel_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_b(tr(lang, 'cancel'), "ar:qcancel", "danger")]])


def room_markup(code: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_b(tr(lang, 'start'), f"ar:rstart:{code}", "success")],
                                 [_b(tr(lang, 'cancel'), f"ar:rcancel:{code}", "danger")]])


def _end_markup(game: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_b(tr(lang, 'play_again'), f"ar:game:{game}", "success")],
                                 [_b(tr(lang, 'menu'), "ar:home")]])


# ----------------------------------------------------------------------------- views
async def _safe_edit(q, text: str, markup=None) -> None:
    try:
        await q.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.debug("arena edit failed: %s", e)


async def _send(app: Application, chat_id: int, text: str, markup=None):
    return await app.bot.send_message(chat_id, text, reply_markup=markup, parse_mode=ParseMode.HTML)


def _home_text(lang: str) -> str:
    return tr(lang, 'home')


def _turn_name(m: Match) -> str:
    return m.names.get(m.state['players'][m.state['turn']], '?')


def _render(m: Match, uid: int) -> tuple[str, InlineKeyboardMarkup | None]:
    lang = m.langs.get(uid, 'en')
    s = m.state
    if m.finished:
        return _result_text(m, uid), _end_markup(m.game, lang)
    if m.game == 'tictactoe':
        mark = {' ': '▫️', 'X': '❌', 'O': '⭕'}
        rows = [[(mark[s['board'][r * 3 + c]], str(r * 3 + c)) for c in range(3)] for r in range(3)]
        head = tr(lang, 'ttt_bot_vs') if s['vs_bot'] else (
            tr(lang, 'ttt_your_move') if s['players'][s['turn']] == uid
            else tr(lang, 'ttt_their_move', name=_turn_name(m)))
        return head, _grid(m.mid, rows)
    if m.game == 'memory':
        visible = set(s['revealed']) | set(s['matched'])
        rows = [[(s['cards'][i] if i in visible else '🂠', str(i)) for i in range(r * 4, r * 4 + 4)] for r in range(3)]
        scores = ' · '.join(f"{m.names.get(p, '?')}: {s['scores'][str(p)]}" for p in s['players'])
        text = f"{tr(lang, 'mem_head')}\n{tr(lang, 'mem_turn', name=_turn_name(m))}\n{scores}"
        return text, _grid(m.mid, rows)
    if m.game == 'quiz':
        r = s['round']
        q = s['questions'][r]
        choices = [(str(c), f"{r}:{i}") for i, c in enumerate(q['choices'])]
        rows = [choices[i:i + 2] for i in range(0, len(choices), 2)]
        text = (f"{tr(lang, 'quiz_head', n=r + 1, total=len(s['questions']), secs=ROUND_SECONDS)}\n\n"
                f"{q['prompt']}\n\n"
                f"{tr(lang, 'quiz_status', done=len(s['answers']), players=len(s['players']))}")
        return text, _grid(m.mid, rows)
    if m.game == 'scramble':
        return tr(lang, 'scr_head', word=s['scrambled']), None
    if m.game == 'codebreaker':
        lines = [f"• {m.names.get(a['player'], '?')}: {a['guess']} → ✅{a['exact']} 🔸{a['misplaced']}"
                 for a in s['attempts'][-6:]]
        text = f"{tr(lang, 'cb_head')}\n{tr(lang, 'cb_turn', name=_turn_name(m)) if len(s['players']) > 1 else ''}\n\n"
        text += "\n".join(lines) if lines else tr(lang, 'cb_empty')
        return text.strip(), None
    return tr(lang, 'expired'), None


def _grid(mid: int, rows) -> InlineKeyboardMarkup:
    """Buttons for a board. Callback data: ar:act:<match id>:<value>."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"ar:act:{mid}:{value}")
                                  for label, value in row] for row in rows])


def _result_text(m: Match, uid: int) -> str:
    lang = m.langs.get(uid, 'en')
    res = m.state.get('_result') or {}
    s = m.state
    if m.game == 'tictactoe':
        w = res.get('winner')
        summary = tr(lang, 'res_draw') if w == 'draw' else tr(lang, 'res_bot') if w == 'bot' else \
            tr(lang, 'res_winner', name=m.names.get(w, '?')) if w is not None else ''
    elif m.game in ('scramble', 'codebreaker'):
        w = res.get('winner')
        summary = tr(lang, 'res_winner', name=m.names.get(w, '?')) if w is not None else ''
    else:
        scores = res.get('scores', s.get('scores', {}))
        summary = tr(lang, 'res_scores', scores=' · '.join(f"{m.names.get(p, '?')}: {scores.get(str(p), 0)}"
                                                           for p in s['players']))
    mine = m.state.get('_outcome', {}).get(uid)
    verdict = {'win': tr(lang, 'res_win'), 'loss': tr(lang, 'res_loss'), 'draw': tr(lang, 'res_draw')}.get(mine, '')
    rew = m.state.get('_rewards', {}).get(uid, '')
    parts = [tr(lang, 'res_head'), summary, verdict, rew]
    if m.state.get('_abandoned'):
        parts = [tr(lang, 'res_ended')]
    return "\n".join(p for p in parts if p)


# ----------------------------------------------------------------------------- match lifecycle
async def _show(app: Application, m: Match) -> None:
    """Send or update one message per player with the current board."""
    for uid in m.players:
        text, markup = _render(m, uid)
        if uid in m.msgs:
            chat_id, msg_id = m.msgs[uid]
            try:
                await app.bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id,
                                                reply_markup=markup, parse_mode=ParseMode.HTML)
                continue
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    continue
            except Exception:
                log.exception("arena: could not edit match %s for %s", m.mid, uid)
        try:
            sent = await _send(app, uid, text, markup)
            m.msgs[uid] = (uid, sent.message_id)
        except Exception:
            log.exception("arena: could not send match %s to %s", m.mid, uid)


def _arm_timer(app: Application, m: Match) -> None:
    current = asyncio.current_task()
    if m.timer and not m.timer.done() and m.timer is not current:
        m.timer.cancel()
    m.timer = None
    if m.game == 'quiz' and not m.finished:
        m.timer = asyncio.create_task(_quiz_timeout(app, m, m.state['round']))


async def _quiz_timeout(app: Application, m: Match, rnd: int) -> None:
    try:
        await asyncio.sleep(ROUND_SECONDS)
        async with m.lock:
            if m.finished or m.state['round'] != rnd:
                return
            m.last = time.monotonic()
            ag.Quiz().skip_round(m.state)
            await _after_move(app, m, timed_out=True)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("arena: quiz timer error")


async def _after_move(app: Application, m: Match, timed_out: bool = False) -> None:
    result = GAMES[m.game].get_result(m.state)
    if result is not None:
        await _finish(app, m, result)
        return
    if timed_out:
        for uid in m.players:
            try:
                await app.bot.send_message(uid, tr(m.langs.get(uid, 'en'), 'time_up'))
            except Exception:
                pass
    await _show(app, m)
    _arm_timer(app, m)


async def _finish(app: Application, m: Match, result: dict, abandoned: bool = False) -> None:
    if m.finished:
        return
    m.finished = True
    m.ended_at = time.monotonic()
    if m.timer and m.timer is not asyncio.current_task() and not m.timer.done():
        m.timer.cancel()
    for uid in m.players:
        if USER_MATCH.get(uid) == m.mid:
            USER_MATCH.pop(uid, None)
    m.state['_result'] = result
    if abandoned:
        m.state['_abandoned'] = True
    else:
        try:
            verdict = outcome(m.game, m.state, result)
        except (KeyError, TypeError, ValueError):
            log.exception("arena: could not work out outcome for match %s", m.mid)
            verdict = {}
        m.state['_outcome'] = verdict
        m.state['_rewards'] = await _reward(app, m, verdict)
    await _show(app, m)


async def _reward(app: Application, m: Match, verdict: dict) -> dict:
    store = app.bot_data["store"]
    out = {}
    multi = len(m.players) > 1 and not m.state.get('vs_bot')
    for uid, v in verdict.items():
        rec, prof = await _load(app, _UserStub(uid))
        if v == 'win':
            xp, coins, d_elo = 30, 40, 20
        elif v == 'loss':
            xp, coins, d_elo = 10, 15, -12
        else:
            xp, coins, d_elo = 15, 20, 0
        prof['xp'] += xp
        prof['coins'] += coins
        prof['played'] += 1
        prof['wins'] += int(v == 'win')
        if multi:
            prof['elo'] = max(0, prof['elo'] + d_elo)
        st = prof['stats'].setdefault(m.game, {'played': 0, 'wins': 0})
        st['played'] += 1
        st['wins'] += int(v == 'win')
        store.save(uid)
        elo_txt = (f" · ELO {'+' if d_elo >= 0 else ''}{d_elo}" if multi else "")
        out[uid] = tr(m.langs.get(uid, 'en'), 'rew', xp=xp, coins=coins, elo=elo_txt)
    return out


class _UserStub:
    """Lets _load() reuse the Telegram user shape with only an id (the record is already cached)."""
    def __init__(self, uid: int) -> None:
        self.id, self.username = uid, None


async def _start_match(app: Application, game: str, mode: str, players: list[int], **kw) -> Match:
    recs = {}
    for uid in players:
        recs[uid] = await _load(app, _UserStub(uid))
    names = {uid: _name_of(recs[uid][0], uid) for uid in players}
    langs = {uid: recs[uid][1]['lang'] for uid in players}
    rules = GAMES[game]
    if game == 'quiz':
        pool = QUIZ_QUESTIONS.get(langs[players[0]], QUIZ_QUESTIONS['en'])
        questions = copy.deepcopy(random.sample(pool, min(5, len(pool))))
        state = rules.start(players, questions)
    elif game == 'tictactoe':
        state = rules.start(players, difficulty=kw.get('difficulty', 'medium'))
    else:
        state = rules.start(players)
    if game == 'tictactoe' and len(players) == 1:
        state['vs_bot'] = True
    m = Match(mid=next(_ids), game=game, mode=mode, players=list(players), state=state, names=names, langs=langs)
    if len(MATCHES) > 200:
        for mid, old in list(MATCHES.items()):
            if old.finished and time.monotonic() - old.ended_at > MATCH_KEEP:
                MATCHES.pop(mid, None)
    MATCHES[m.mid] = m
    for uid in players:
        USER_MATCH[uid] = m.mid
    await _show(app, m)
    _arm_timer(app, m)
    return m


def _active_match(uid: int) -> Match | None:
    mid = USER_MATCH.get(uid)
    m = MATCHES.get(mid) if mid else None
    return m if m and not m.finished else None


async def _ensure_free(app: Application, uid: int) -> bool:
    """True if the user may start a new game. An idle old match is ended so it can't block them forever."""
    m = _active_match(uid)
    if m is None:
        return True
    if time.monotonic() - m.last > IDLE_SECONDS:
        await _finish(app, m, {}, abandoned=True)
        return True
    return False


# ----------------------------------------------------------------------------- queue & rooms
def _enqueue(uid: int, elo: int, game: str, mode: str, need: int) -> list[int] | None:
    q = QUEUES.setdefault((game, mode), [])
    now = time.time()
    q[:] = [e for e in q if e['uid'] != uid]
    picked = []
    for e in q:
        window = 100 + int(max(0.0, now - e['at']) // 10) * 100
        if abs(elo - e['elo']) <= window:
            picked.append(e)
            if len(picked) == need - 1:
                break
    if len(picked) == need - 1:
        for e in picked:
            q.remove(e)
        return [uid] + [e['uid'] for e in picked]
    q.append({'uid': uid, 'elo': elo, 'at': now})
    return None


def _leave_queues(uid: int) -> None:
    for q in QUEUES.values():
        q[:] = [e for e in q if e['uid'] != uid]


def _purge_rooms() -> None:
    now = time.time()
    for code, r in list(ROOMS.items()):
        if now - r.created > ROOM_SECONDS:
            ROOMS.pop(code, None)
            for uid in r.members:
                if USER_ROOM.get(uid) == code:
                    USER_ROOM.pop(uid, None)


def _new_code() -> str:
    while True:
        code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        if code not in ROOMS:
            return code


# ----------------------------------------------------------------------------- Telegram handlers
async def msg_arena(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rec, prof = await _load(context.application, update.effective_user)
    await update.effective_message.reply_text(_home_text(prof['lang']), reply_markup=home_markup(prof['lang']),
                                              parse_mode=ParseMode.HTML)


async def on_arena_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    uid = q.from_user.id
    parts = q.data.split(":")
    action = parts[1]
    app = context.application
    answered = False

    async def alert(text: str) -> None:              # a pop-up answers the tap; only one answer per tap
        nonlocal answered
        answered = True
        try:
            await q.answer(text, show_alert=True)
        except BadRequest:
            pass

    try:
        rec, prof = await _load(app, q.from_user)
        lang = prof['lang']
        if action == 'home':
            await _safe_edit(q, _home_text(lang), home_markup(lang))
        elif action == 'games':
            await _safe_edit(q, tr(lang, 'choose_game'), games_markup(lang))
        elif action == 'game':
            game = parts[2]
            if game not in GAMES:
                return
            await _safe_edit(q, tr(lang, 'choose_mode', game=tr(lang, GAME_LABEL_KEY[game])), modes_markup(game, lang))
        elif action == 'mode':
            game, mode = parts[2], parts[3]
            if game not in GAMES or mode not in ('single', 'duo', 'squad'):
                return
            if mode == 'single':
                if game == 'tictactoe':
                    await _safe_edit(q, tr(lang, 'ai_level'), difficulty_markup(lang))
                else:
                    if not await _ensure_free(app, uid):
                        return await alert(tr(lang, 'busy'))
                    await _start_match(app, game, 'single', [uid])
                    await _safe_edit(q, tr(lang, GAME_LABEL_KEY[game]) + " · " + tr(lang, 'single'))
            else:
                await _safe_edit(q, f"{tr(lang, GAME_LABEL_KEY[game])} · {tr(lang, mode)}", queue_markup(game, mode, lang))
        elif action == 'diff':
            if not await _ensure_free(app, uid):
                return await alert(tr(lang, 'busy'))
            await _start_match(app, 'tictactoe', 'single', [uid], difficulty=parts[3])
            await _safe_edit(q, tr(lang, GAME_LABEL_KEY['tictactoe']) + " · " + tr(lang, 'single'))
        elif action == 'queue':
            game, mode = parts[2], parts[3]
            if game not in GAMES or mode not in ('duo', 'squad'):
                return
            if USER_ROOM.get(uid) or not await _ensure_free(app, uid):
                return await alert(tr(lang, 'busy'))
            need = 4 if mode == 'squad' else 2
            players = _enqueue(uid, prof['elo'], game, mode, need)
            if players:
                await _start_match(app, game, mode, players)
            else:
                await _safe_edit(q, tr(lang, 'queue_wait'), cancel_markup(lang))
        elif action == 'qcancel':
            _leave_queues(uid)
            await _safe_edit(q, tr(lang, 'queue_cancelled'), home_markup(lang))
        elif action == 'room':
            game, mode = parts[2], parts[3]
            if game not in GAMES or mode not in ('duo', 'squad'):
                return
            if not await _ensure_free(app, uid):
                return await alert(tr(lang, 'busy'))
            _purge_rooms()
            old = USER_ROOM.pop(uid, None)
            if old:
                ROOMS.pop(old, None)
            code = _new_code()
            need = 4 if mode == 'squad' else 2
            ROOMS[code] = Room(code, uid, game, mode, need, [uid], {uid: _name_of(rec, uid)})
            USER_ROOM[uid] = code
            await _safe_edit(q, tr(lang, 'room_created', code=code, count=1, need=need), room_markup(code, lang))
        elif action == 'rstart':
            await _room_start(app, q, parts[2], uid, lang, alert)
        elif action == 'rcancel':
            code = parts[2]
            room = ROOMS.get(code)
            if not room or room.host != uid:
                return await alert(tr(lang, 'room_host_only'))
            ROOMS.pop(code, None)
            for member in room.members:
                USER_ROOM.pop(member, None)
                if member != uid:
                    try:
                        await app.bot.send_message(member, tr(prof_lang(app, member), 'room_closed_member', code=code))
                    except Exception:
                        pass
            await _safe_edit(q, tr(lang, 'room_cancelled'), home_markup(lang))
        elif action == 'act':
            answered = await _game_action(app, q, uid, parts, alert)
        elif action == 'prof':
            await _safe_edit(q, tr(lang, 'profile_txt', name=_name_of(rec, uid), level=_level(prof['xp']),
                                   xp=prof['xp'], coins=prof['coins'], elo=prof['elo'], played=prof['played'],
                                   wins=prof['wins'], streak=prof['streak']), home_markup(lang))
        elif action == 'lb':
            await _safe_edit(q, _leaderboard_text(app, lang), home_markup(lang))
        elif action == 'daily':
            await _safe_edit(q, _claim_daily(app, rec, prof, uid, lang), home_markup(lang))
        elif action == 'lang':
            new = parts[2] if len(parts) > 2 else ('bn' if lang == 'en' else 'en')
            prof['lang'] = new if new in S else 'en'
            app.bot_data["store"].save(uid)
            await _safe_edit(q, tr(prof['lang'], 'lang_set') + "\n\n" + _home_text(prof['lang']),
                             home_markup(prof['lang']))
        elif action == 'help':
            await _safe_edit(q, tr(lang, 'help_text'), home_markup(lang))
    finally:
        if not answered:
            try:
                await q.answer()
            except BadRequest:
                pass


async def _room_start(app: Application, q, code: str, uid: int, lang: str, alert) -> None:
    room = ROOMS.get(code)
    if not room:
        return await _safe_edit(q, tr(lang, 'room_not_found'), home_markup(lang))
    if room.host != uid:
        return await alert(tr(lang, 'room_host_only'))
    if len(room.members) < room.need:
        return await _safe_edit(q, tr(lang, 'room_need', need=room.need, count=len(room.members))
                                + "\n\n" + tr(lang, 'room_created', code=code, count=len(room.members), need=room.need),
                                room_markup(code, lang))
    players = room.members[:room.need]
    left_out = room.members[room.need:]
    ROOMS.pop(code, None)
    for p in room.members:
        USER_ROOM.pop(p, None)
    for p in left_out:
        try:
            await app.bot.send_message(p, tr(prof_lang(app, p), 'room_started_out'))
        except Exception:
            pass
    if any(_active_match(p) for p in players):
        return await _safe_edit(q, tr(lang, 'busy'), home_markup(lang))
    await _start_match(app, room.game, room.mode, players)


async def _game_action(app: Application, q, uid: int, parts: list[str], alert) -> bool:
    """Returns True if a pop-up answer was already sent for this tap."""
    try:
        mid = int(parts[2])
    except (IndexError, ValueError):
        return False
    m = MATCHES.get(mid)
    if m is None or m.finished:
        await alert(tr('en', 'expired'))
        return True
    if uid not in m.players:
        await alert(tr(m.langs.get(uid, 'en'), 'not_yours'))
        return True
    value = ":".join(parts[3:])
    return await _apply_move(app, m, uid, value, reply=alert)


async def _apply_move(app: Application, m: Match, uid: int, value: str, reply) -> bool:
    """Apply one move. `reply(text)` is a coroutine that tells the player why a move was refused.
    Returns True if `reply` was used."""
    lang = m.langs.get(uid, 'en')
    async with m.lock:
        if m.finished:
            await reply(tr(lang, 'expired'))
            return True
        if m.game == 'quiz':
            rnd, _, choice = value.partition(":")
            if not rnd.isdigit() or int(rnd) != m.state['round']:
                await reply(tr(lang, 'round_passed'))
                return True
            value = choice
        before = json.dumps(m.state, sort_keys=True)
        m.state = GAMES[m.game].handle_action(m.state, uid, value)
        if json.dumps(m.state, sort_keys=True) == before:
            if m.game == 'quiz' and str(uid) in m.state['answers']:
                await reply(tr(lang, 'already_answered'))
            elif m.game in ('tictactoe', 'memory', 'codebreaker') and len(m.players) > 1 \
                    and m.state['players'][m.state['turn']] != uid:
                await reply(tr(lang, 'not_your_turn'))
            else:
                await reply(tr(lang, 'invalid'))
            return True
        m.last = time.monotonic()
        await _after_move(app, m)
        return False


async def _room_join(app: Application, update: Update, code: str) -> None:
    user = update.effective_user
    rec, prof = await _load(app, user)
    lang = prof['lang']
    room = ROOMS.get(code)
    _purge_rooms()
    if not room:
        return await update.effective_message.reply_text(tr(lang, 'room_not_found'))
    if user.id in room.members:
        return await update.effective_message.reply_text(tr(lang, 'room_already'))
    if len(room.members) >= room.need:
        return await update.effective_message.reply_text(tr(lang, 'room_full'))
    if _active_match(user.id) or USER_ROOM.get(user.id):
        return await update.effective_message.reply_text(tr(lang, 'busy'))
    room.members.append(user.id)
    room.names[user.id] = _name_of(rec, user.id)
    USER_ROOM[user.id] = code
    await update.effective_message.reply_text(
        tr(lang, 'room_joined_you', code=code, count=len(room.members), need=room.need), parse_mode=ParseMode.HTML)
    try:
        await app.bot.send_message(room.host, tr(prof_lang(app, room.host), 'room_joined_host',
                                                 name=room.names[user.id], count=len(room.members), need=room.need),
                                   parse_mode=ParseMode.HTML)
    except Exception:
        pass


def prof_lang(app: Application, uid: int) -> str:
    rec = app.bot_data["store"].peek(uid) or {}
    return profile_of(rec)['lang'] if rec else 'en'


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = (update.effective_message.text or "").partition(" ")[2].strip().upper()
    if not code:
        return await update.effective_message.reply_text("Usage: /join CODE")
    await _room_join(context.application, update, code)


async def cmd_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    rec, prof = await _load(context.application, update.effective_user)
    lang = prof['lang']
    m = _active_match(uid)
    if m is None or m.game != 'codebreaker':
        return await update.effective_message.reply_text(tr(lang, 'guess_hint'), parse_mode=ParseMode.HTML)
    guess = (update.effective_message.text or "").partition(" ")[2].strip()
    if len(guess) != 4 or not guess.isdigit():
        return await update.effective_message.reply_text(tr(lang, 'guess_wrong_len'))
    await _apply_move(context.application, m, uid, guess,
                      reply=lambda txt: update.effective_message.reply_text(txt))


async def on_arena_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scramble answers are typed in the chat: only while the user has an active Scramble match."""
    uid = update.effective_user.id if update.effective_user else None
    if uid is None:
        return
    m = _active_match(uid)
    if m is None or m.game != 'scramble':
        return
    text = (update.effective_message.text or "").strip()
    if not text or text.startswith("/"):
        return
    await _apply_move(context.application, m, uid, text,
                      reply=lambda txt: update.effective_message.reply_text(txt))


def _leaderboard_text(app: Application, lang: str) -> str:
    store = app.bot_data["store"]
    rows = []
    for uid, rec in store.export_all().items():
        if "arena" not in rec:
            continue
        p = profile_of(rec)
        rows.append((p['elo'], _name_of(rec, uid), p['wins']))
    rows.sort(reverse=True)
    if not rows:
        return tr(lang, 'lb_empty')
    lines = [tr(lang, 'lb_title')]
    for i, (elo, name, wins) in enumerate(rows[:10], 1):
        lines.append(f"{i}. {html.escape(name)} — ELO {elo} · wins {wins}")
    return "\n".join(lines)


def _claim_daily(app: Application, rec: dict, prof: dict, uid: int, lang: str) -> str:
    now = time.time()
    last = prof['daily_at']
    if last and now - last < 86400:
        left = int(86400 - (now - last))
        return tr(lang, 'daily_wait', h=left // 3600, m=(left % 3600) // 60)
    prof['streak'] = prof['streak'] + 1 if last and now - last < 172800 else 1
    coins = 50 + min(prof['streak'] - 1, 7) * 10
    prof['coins'] += coins
    prof['xp'] += 20
    prof['daily_at'] = now
    app.bot_data["store"].save(uid)
    return tr(lang, 'daily_ok', coins=coins, streak=prof['streak'])


# ----------------------------------------------------------------------------- registration
def register(app: Application, button_label: str, is_menu_text) -> None:
    """Wire the Arena into the bot. `button_label` is the bottom-menu 🎮 button text."""
    import re
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(button_label)}$"), msg_arena))
    app.add_handler(CommandHandler("arena", msg_arena))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("guess", cmd_guess))
    app.add_handler(CallbackQueryHandler(on_arena_callback, pattern=r"^ar:"))
    # group 2 runs after the bot's own text handler (group 1) and never swallows menu buttons
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _guard(on_arena_text, is_menu_text)), group=2)


def _guard(handler, is_menu_text):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if is_menu_text((update.effective_message.text or "").strip()):
            return
        await handler(update, context)
    return wrapped
