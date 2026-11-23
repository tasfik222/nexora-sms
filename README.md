# Nexora Telegram Bot

[![Version](https://img.shields.io/badge/version-2.0.0-blue?style=for-the-badge)](https://github.com/tasfik222/nexora-sms/tree/main/bot-2.0.0)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/8unNuUkyE9)

A Python Telegram bot that gives users temporary phone numbers from SMS provider APIs, delivers every incoming OTP straight into Telegram, and pays a small reward for each SMS received. It includes an AI support chat, an admin panel, withdrawal requests, live traffic stats, backups, idle reminders and a built-in mini-game arena.

> **Latest version: `2.0.0`** — see [`bot-2.0.0`](https://github.com/tasfik222/nexora-sms/tree/main/bot-2.0.0). Older folders (`files`, `files v2` … `file v10 beta`, `bot-0.12.0`) are kept for history.

---

## Table of contents

- [Features](#features)
- [What's new in 2.0.0](#whats-new-in-200)
- [What's new in 1.0.6](#whats-new-in-106)
- [What's new in 1.0.5](#whats-new-in-105)
- [What's new in 1.0.4](#whats-new-in-104)
- [What's new in 1.0.3](#whats-new-in-103)
- [What's new in 1.0.2](#whats-new-in-102)
- [What's new in 1.0.0](#whats-new-in-100)
- [What's new in 0.13.2](#whats-new-in-0132)
- [What's new in 0.13.1](#whats-new-in-0131)
- [What's new in 0.13.0](#whats-new-in-0130)
- [What's new in 0.12.0](#whats-new-in-0120)
- [Project structure](#project-structure)
- [Requirements](#requirements)
- [Setup](#setup)
- [Environment variables](#environment-variables)
- [AI Support setup](#ai-support-setup)
- [How it works](#how-it-works)
- [Commands](#commands)
- [Data and secrets](#data-and-secrets)
- [Tests](#tests)
- [Disclaimer](#disclaimer)
- [License & author](#license--author)

---

## Features

### For users

- **Get Number** — pick a server, a service (WhatsApp, Telegram, Facebook, …) and a range, then receive number(s) instantly.
- **Automatic OTP delivery** — the bot checks for new SMS every 5 seconds for up to 20 minutes. Every new SMS on the number is delivered and rewarded, not just the first one.
- **Change Number** — get a new number from the same range with one tap. The new number arrives as a fresh message at the bottom of the chat, and the previous message is removed.
- **Balance** — rewards are stored in paisa (100 paisa = ৳1), so there are no floating-point errors.
- **Withdraw** — request a payout via bKash, Nagad or Rocket. The minimum is configurable.
- **Top Traffic** — the busiest ranges of the last N minutes. Tap one to order from it.
- **Leaderboard** — top 10 users by OTPs, daily (resets at 12:00 AM) and lifetime.
- **Invite friends (`/invite`)** — share your link. You get a share of the rewards your friends earn, every time they get a code. Your friends don't see who invited them.
- **Badges (`/badges`)** — badges for codes received and Arena wins.
- **Arena (`/game`)** — mini-games: Quiz, Word Scramble, Code Breaker, Tic-Tac-Toe and Memory. Quiz questions and scramble words are refreshed by AI when an AI key is set. Single, duo and squad modes, private rooms, XP, ELO and daily rewards. English and Bangla. Game coins are separate from the money balance and never change it.
- **AI Support (`/ai`)** — a chat assistant for questions about numbers, OTPs, rewards, balance, withdrawals, how to use the bot, and general or study questions. It remembers the recent conversation, and each user has a daily question limit.
- **Idle reminders** — if you have been away for a while during the day, the bot tells you the busiest range right now. You can turn this off at any time (see [Commands](#commands)).
- **ℹ️ About** — how it works, the bot version, and the developer's contact details. The **📘 Features** button in About (or `/features`) shows an overview of all versions.

### For admins (`/admin`)

- Review withdrawals: mark as paid or reject (a rejected amount is returned automatically).
- Set the **numbers per range**, the **default reward**, **per-range prices** and **custom range names**.
- **Stop / Start** the bot (maintenance mode with a notice to all users).
- User list with search, ban / unban, and per-user details (`/user <id>`), including **Last seen**.
- Post and pin a **group announcement** with an editable text.
- **Backups** — sent to a private chat and restorable by uploading the `.zip` back to the bot.
- **Admin dashboard** (`/dashboard`) — totals for users, codes, balances, pending withdrawals and referral payouts.
- **Restart timer** (♻️ button) — shows when the bot last started and when the restart reminder is due. **Restarted now** resets the timer.

### Other

- Users must join a required group before they can use the bot.
- In groups the bot stays silent: it does not answer commands typed in a group. Use private chat.
- Optional channel feed that posts every new SMS (numbers are masked by default).
- Two SMS providers are supported (Voltx and Zenex). A provider without an API key is hidden automatically.
- AI Support is enabled only when at least one AI provider key is set. Without a key, the AI button and `/ai` are hidden.

---

## What's new in 2.0.0

- **SQLite database**: user data is stored in `data/nexora.db` instead of one JSON file per user. On the first start with an empty database, the old JSON files are imported once and kept as a backup.
- **Fraud check**: an account that receives more than `FRAUD_MAX_CODES_PER_HOUR` codes (default `60`) in one hour is flagged for review. Its balance is kept, withdrawals are on hold, and every full admin gets an alert.
- **`/unflag <user id>`** (full admins only): clears the review flag so withdrawals work again.
- **Viewer role** (`VIEWER_IDS`): viewers can open the admin panel, user lists, pending and history, `/dashboard` and the restart timer, but cannot change anything.
- **Admin command menu**: admins see `/dashboard`, `/restarttimer` and `/unflag` in their own chat with the bot.
- **Storage switch**: `STORAGE=sqlite` (default) or `STORAGE=json`.

---

## What's new in 1.0.6

- **Leave game**: a 🚪 Leave game button in every Arena match, and `/exitgame`. Leaving ends the match for everyone, with no rewards.
- **Scramble fixes**: wrong guesses show a clear message, and words with fewer than 4 different letters are rejected.

---

## What's new in 1.0.5

- **About lists all commands**: the About page now has a **Commands** section with every command and what it does.

---

## What's new in 1.0.4

- **Features overview is up to date**: the 📘 Features message marks version 1.0.3 as current, and shows 1.0.2 as a separate earlier release.

---

## What's new in 1.0.3

- **Features overview**: the **📘 Features** button in About, and the `/features` command, send a summary of every version and the current features as messages.

---

## What's new in 1.0.2

- **Referral share comes from the friend's reward**: the referrer's 20% is taken from the referred user's reward, so the total paid out stays the same. The referred user is not told who invited them.
- **Referral share on every SMS**: each reward the friend earns gives the referrer a share. Each SMS is paid only once.
- The referrer's share goes to their balance and can be withdrawn.

---

## What's new in 1.0.1

- **Referral earnings are visible to the referrer only**, in `/invite`, which shows the number of friends who joined and the total earned.
- `REFERRAL_PERCENT` sets the share (default `20`).

---

## What's new in 1.0.0

- **Arena games continue after a restart**: running matches are saved and shown again when the bot starts. Turn timers start again from the full time.
- **Faster Arena leaderboard**: the ELO board is rebuilt at most once a minute.
- **Referral system**: `/invite` gives your personal link. You get 20% of every reward your friends earn. The share comes out of the friend's reward, so the total stays the same. Your friends are not told who invited them.
- **Badges**: `/badges` shows badges for codes received and Arena wins.
- **Admin dashboard**: `/dashboard` shows the main totals at a glance.
- **Provider key alert**: if an SMS provider key fails, admins get a message, at most once an hour.
- **Quieter logs**: short network errors are logged as one line.

---

## What's new in 0.13.2

- **Fresh quiz questions and scramble words**: the built-in lists were small, so the same items came up often. When an AI key is set, the bot asks the AI for new questions (English and Bangla) and new words every 30 minutes. Only valid, new items are kept.
- **Less repetition**: questions shown recently are avoided within the next matches when possible.
- **Without an AI key** the arena uses the built-in lists, as before.
- No new environment variables. The same AI keys are used.

---

## What's new in 0.13.1

- **Restart reminder for admins**: the bot records its start time. Three days (72 hours) after each start, every admin gets a reminder to restart the bot. Free hosting stops the bot after about 4 days, so this gives time to restart it. The reminder is sent once per start.
- **♻️ Restart timer** in the admin panel: shows the last start time and the reminder time. Tap **Restarted now** after a manual restart to reset the timer to today.
- **`/restarttimer`** (admins only): shows the same status as text.

---

## What's new in 0.13.0

- **AI Support chat** with the 🤖 **AI Support** button or `/ai`. Use **🧹 New chat** to start over and **❌ End chat** to close it. Opening another menu button also ends the chat.
- **Four AI providers**: Gemini, Groq, OpenRouter and Anthropic. The bot picks the first one that has a key, or the one set in `AI_PROVIDER`.
- **Daily question limit** (`AI_DAILY_LIMIT`, default `30`), the same for every user. Admins are not limited.
- **Safer AI answers**: the AI cannot change balances, accounts or withdrawals. Payment and ban issues are sent to human support.
- **Long chats no longer get stuck** after many questions.
- **Busy notice**: if you send a new message while the previous answer is still being written, the bot tells you to wait instead of ignoring it.
- **Version** shown in About is now `0.13.0`.

---

## What's new in 0.12.0

- **Arena mini-games** with `/game`: five games, three modes, private rooms, ELO, XP and daily rewards.
- **Idle reminders** with the busiest range, sent between 9:00 and 23:00 Bangladesh time, at most once per idle stretch. Users can switch them off with the button in the message or with `/reminders`.
- **About** replaces the old Help button. `/help` still works.
- **Change Number** sends the new number as a new message and removes the previous one.
- **Several SMS per number**: after the first OTP, the bot keeps watching that number for the full 20 minutes and pays each new SMS.
- **Groups are silent**: the bot no longer replies to commands inside groups.
- **Version** is shown in About.
- **Last seen** for each user, visible to admins in the user details.

---

## Project structure

| File | Purpose |
|---|---|
| `bot.py` | Main Telegram bot: menus, number flow, SMS polling, withdrawals, admin panel |
| `ai_support.py` | AI Support chat: providers, history, daily limit, busy handling |
| `api_client.py` | Provider clients (Voltx, Zenex) behind one common interface |
| `storage.py` | User records (balance, history, bans, last seen) with duplicate-credit protection |
| `storage_sqlite.py` | SQLite backend for user records, and the one-time JSON import |
| `fraud.py` | Flags accounts that receive too many codes in one hour |
| `withdrawals.py` | Withdrawal requests and their status |
| `settings.py` | Admin-controlled settings, saved in `data/settings.json` |
| `traffic.py` | Top-traffic tracking from the live console |
| `console_api.py` | Reads live console entries from the providers |
| `nudge.py` | Idle reminders for users who have been away during the day |
| `restart_watch.py` | Restart reminder: tells admins to restart the bot 3 days after each start |
| `arena.py` | Telegram side of the mini-game arena (menus, matches, rooms, rewards) |
| `arena_games.py` | Game rules for the arena (pure Python, no Telegram) |
| `arena_quiz_data.py` | Built-in quiz questions in English and Bangla |
| `features.py` | Referral share and badges |
| `features_text.py` | Feature overview messages (the 📘 Features button) |
| `arena_content.py` | Refreshes quiz questions and scramble words with AI, with fallback to built-in lists |
| `channel_feed.py` | Optional channel feed of new SMS |
| `otp_format.py` | Formats the OTP messages sent to users |
| `backup.py` | Creates and restores data backups |
| `tests/` | Automated tests |

---

## Requirements

- Python 3.10+
- [`python-telegram-bot`](https://python-telegram-bot.org) `>= 22.7`
- [`httpx`](https://www.python-httpx.org) `>= 0.25`

No separate database server is needed. User data is stored in a local SQLite file (`data/nexora.db`).

---

## Setup

```bash
git clone https://github.com/tasfik222/nexora-telegram-bot-python-code.git
cd nexora-telegram-bot-python-code/bot-2.0.0

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Set the environment variables (see below), then start the bot:

```bash
python bot.py
```

Example:

```bash
export TELEGRAM_BOT_TOKEN="123456:YOUR_TOKEN_HERE"
export ZENEX_API_KEY="your_key"
export GEMINI_API_KEY="your_gemini_key"
export ADMIN_IDS="123456789"
python bot.py
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `VOLTXSMS_API_KEY` | one of the two | Voltx provider key |
| `ZENEX_API_KEY` | one of the two | Zenex provider key |
| `ADMIN_IDS` | recommended | Comma-separated Telegram user IDs of the full admins. Without it, the admin panel falls back to the built-in default ID |
| `VIEWER_IDS` | optional | Comma-separated Telegram user IDs that can look at the admin panel and dashboard but cannot change anything |
| `STORAGE` | optional | `sqlite` (default) or `json` |
| `FRAUD_MAX_CODES_PER_HOUR` | optional | Codes per account per hour before the account is flagged (default `60`) |
| `REQUIRED_GROUP_ID` | optional | Group users must join first. Use `off` to disable. The bot must be an admin of that group |
| `REQUIRED_GROUP_LINK` | optional | Invite link for the Join button |
| `CHANNEL_ID` | optional | Channel that receives every new SMS (bot must be a channel admin) |
| `BACKUP_CHAT_ID` | optional | Private chat or channel where backups are sent and pinned |
| `CONSOLE_GROUP_LINK` | optional | Link opened by the Console button |
| `SUPPORT_LINK` | optional | Link opened by the Support button |
| `OTP_GROUP_LINK` | optional | Shows an "OTP Group" button |
| `WITHDRAW_MIN_TAKA` | optional | Minimum withdrawal in taka (default `50`) |
| `WITHDRAW_METHODS` | optional | Payout methods (default `bKash,Nagad,Rocket`) |
| `TRAFFIC_WINDOW_MIN` | optional | Minutes of history for Top Traffic (default `30`) |
| `FEED_MASK` | optional | Set to `0` to show full numbers in the console view and feed (masked by default) |
| `NUDGE_IDLE_HOURS` | optional | Hours of inactivity before a reminder (default `3`) |
| `NUDGE_START_HOUR` | optional | Reminders start at this hour, Bangladesh time (default `9`) |
| `NUDGE_END_HOUR` | optional | Reminders stop at this hour, Bangladesh time (default `23`) |
| `NUDGE_CHECK_SECONDS` | optional | How often idle users are checked (default `900`) |
| `GEMINI_API_KEY` | one AI key | Google Gemini key (default model `gemini-2.5-flash`) |
| `GROQ_API_KEY` | one AI key | Groq key (default model `openai/gpt-oss-20b`) |
| `OPENROUTER_API_KEY` | one AI key | OpenRouter key (default model `meta-llama/llama-3.3-70b-instruct:free`) |
| `ANTHROPIC_API_KEY` | one AI key | Anthropic key (default model `claude-haiku-5-5`) |
| `AI_PROVIDER` | optional | Force one provider: `gemini`, `groq`, `openrouter` or `anthropic` |
| `AI_MODEL` | optional | Override the default model of the chosen provider |
| `AI_DAILY_LIMIT` | optional | Questions per user per day (default `30`). Admins are not limited |
| `AI_ONLY_BOT` | optional | Set to `1` to answer only bot-related questions. Other questions get a polite off-topic reply |
| `REFERRAL_PERCENT` | optional | Share of each reward that goes to the referrer (default `20`). It is taken from the referred user's reward |
| `RESTART_REMIND_HOURS` | optional | Hours after a start before admins get the restart reminder (default `72`) |
| `RESTART_CHECK_SECONDS` | optional | How often the restart timer is checked (default `1800`) |
| `LOCAL_DATA_DIR` | optional | Where user data is saved (default `./data`). The database is `nexora.db` in this folder |

---

## AI Support setup

1. Get a key from one provider (for example Gemini at Google AI Studio).
2. Set it as an environment variable, such as `GEMINI_API_KEY`.
3. Restart the bot. The **🤖 AI Support** button and `/ai` command appear automatically. The same key also refreshes the arena's quiz questions and word scramble words.

If you have more than one key, set `AI_PROVIDER` to choose which one is used. Check the model name with your provider and set `AI_MODEL` if it changes.

---

## How it works

1. The user taps **Get Number**, then chooses a service and a range.
2. The bot orders number(s) from the selected provider.
3. It polls the provider's OTP endpoint every 5 seconds for up to 20 minutes.
4. Each new SMS is sent to the user and the range's reward is added to their balance. The same SMS is never paid twice.
5. When the balance reaches the minimum, the user can submit a withdrawal request. An admin reviews it in the admin panel.

---

## Commands

| Command / button | What it does |
|---|---|
| `/start` | Opens the main menu |
| `/balance` | Shows your balance |
| `/game` | Opens the mini-game arena |
| `/ai` | Opens the AI Support chat (private chat only) |
| `/reminders` | Turn idle reminders on or off |
| `/console` | Opens the live console group |
| `/support` | Contact support |
| `/withdraw` | Request a withdrawal |
| `/help` | Same as the **ℹ️ About** button |
| `/features` | Feature overview, sent as messages |
| `/invite` | Your invite link and referral earnings |
| `/badges` | Your badges |
| `/restarttimer` | Shows the restart timer status (admins only) |
| `/dashboard` | Totals overview (admins only) |
| `/admin` | Admin panel (admins only) |
| `/user <id>` | User details (admins only) |
| `/unflag <id>` | Clears a fraud review and reopens withdrawals (full admins only) |

---

## Hosting and restarts

Free hosting stops the bot after about 4 days, so it must be restarted by hand. The start time is saved in `data/settings.json`. Keep `data/` on persistent storage. If that folder is wiped on restart, the timer and user data are lost. Admins get a reminder 3 days after each start, and can also reset the timer from the admin panel.

---

## Data and secrets

- Never commit your bot token, provider API keys or a `.env` file.
- The `data/` folder holds real user records, balances, withdrawal details, and running Arena matches (`arena_matches.json`). The user database is `data/nexora.db`. Keep the whole folder out of version control. Add `data/` and `__pycache__/` to `.gitignore`.
- Keep the old `data/users/` JSON folder as a backup until you have checked balances after the first 2.0.0 start. The bot does not delete or change it.

---

## Tests

From the project folder:

```bash
pip install pytest
python -m pytest -q tests
```

---

## Disclaimer

This project is intended for authorized SMS verification workflows only. Use it only with services, numbers and accounts you own or are explicitly allowed to use, and follow the terms of your SMS providers and Telegram.

---

## License & author

Released under the [MIT License](./LICENSE). Copyright (c) 2026 Tasfik Abdullah.

**Tasfik Abdullah**

- GitHub: [@tasfik222](https://github.com/tasfik222)
- Telegram: [@tasfikabdullah6](https://t.me/tasfikabdullah6)
- Email: tasfikonim3@gmail.com

Need a bot, a website, or changes to an existing project? Get in touch.

💬 **Discord:** [Join my Discord community](https://discord.gg/8unNuUkyE9) for communication, information and support.
