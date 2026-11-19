# Nexora Telegram Bot

[![Version](https://img.shields.io/badge/version-0.13.1-blue?style=for-the-badge)](https://github.com/tasfik222/nexora-sms/tree/main/bot-0.13.1)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/8unNuUkyE9)

A Python Telegram bot that gives users temporary phone numbers from SMS provider APIs, delivers every incoming OTP straight into Telegram, and pays a small reward for each SMS received. It includes an AI support chat, an admin panel, withdrawal requests, live traffic stats, backups, idle reminders and a built-in mini-game arena.

> **Latest version: `0.13.1`** — see [`bot-0.13.1`](https://github.com/tasfik222/nexora-sms/tree/main/bot-0.13.1). Older folders (`files`, `files v2` … `file v10 beta`, `bot-0.12.0`) are kept for history.

---

## Table of contents

- [Features](#features)
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
- **Arena (`/game`)** — mini-games: Quiz, Word Scramble, Code Breaker, Tic-Tac-Toe and Memory. Single, duo and squad modes, private rooms, XP, ELO and daily rewards. English and Bangla. Game coins are separate from the money balance and never change it.
- **AI Support (`/ai`)** — a chat assistant for questions about numbers, OTPs, rewards, balance, withdrawals, how to use the bot, and general or study questions. It remembers the recent conversation, and each user has a daily question limit.
- **Idle reminders** — if you have been away for a while during the day, the bot tells you the busiest range right now. You can turn this off at any time (see [Commands](#commands)).
- **ℹ️ About** — how it works, the bot version, and the developer's contact details.

### For admins (`/admin`)

- Review withdrawals: mark as paid or reject (a rejected amount is returned automatically).
- Set the **numbers per range**, the **default reward**, **per-range prices** and **custom range names**.
- **Stop / Start** the bot (maintenance mode with a notice to all users).
- User list with search, ban / unban, and per-user details (`/user <id>`), including **Last seen**.
- Post and pin a **group announcement** with an editable text.
- **Backups** — sent to a private chat and restorable by uploading the `.zip` back to the bot.
- **Restart timer** (♻️ button) — shows when the bot last started and when the restart reminder is due. **Restarted now** resets the timer.

### Other

- Users must join a required group before they can use the bot.
- In groups the bot stays silent: it does not answer commands typed in a group. Use private chat.
- Optional channel feed that posts every new SMS (numbers are masked by default).
- Two SMS providers are supported (Voltx and Zenex). A provider without an API key is hidden automatically.
- AI Support is enabled only when at least one AI provider key is set. Without a key, the AI button and `/ai` are hidden.

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
| `storage.py` | Local per-user records (balance, history, bans, last seen) with duplicate-credit protection |
| `withdrawals.py` | Withdrawal requests and their status |
| `settings.py` | Admin-controlled settings, saved in `data/settings.json` |
| `traffic.py` | Top-traffic tracking from the live console |
| `console_api.py` | Reads live console entries from the providers |
| `nudge.py` | Idle reminders for users who have been away during the day |
| `restart_watch.py` | Restart reminder: tells admins to restart the bot 3 days after each start |
| `arena.py` | Telegram side of the mini-game arena (menus, matches, rooms, rewards) |
| `arena_games.py` | Game rules for the arena (pure Python, no Telegram) |
| `arena_quiz_data.py` | Quiz questions in English and Bangla |
| `channel_feed.py` | Optional channel feed of new SMS |
| `otp_format.py` | Formats the OTP messages sent to users |
| `backup.py` | Creates and restores data backups |
| `tests/` | Automated tests |

---

## Requirements

- Python 3.10+
- [`python-telegram-bot`](https://python-telegram-bot.org) `>= 22.7`
- [`httpx`](https://www.python-httpx.org) `>= 0.25`

No database is needed. User data is stored in JSON files.

---

## Setup

```bash
git clone https://github.com/tasfik222/nexora-telegram-bot-python-code.git
cd nexora-telegram-bot-python-code/bot-0.13.1

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
| `ADMIN_IDS` | recommended | Comma-separated Telegram user IDs of the admins. Without it, the admin panel falls back to the built-in default ID |
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
| `RESTART_REMIND_HOURS` | optional | Hours after a start before admins get the restart reminder (default `72`) |
| `RESTART_CHECK_SECONDS` | optional | How often the restart timer is checked (default `1800`) |
| `LOCAL_DATA_DIR` | optional | Where user data is saved (default `./data`) |

---

## AI Support setup

1. Get a key from one provider (for example Gemini at Google AI Studio).
2. Set it as an environment variable, such as `GEMINI_API_KEY`.
3. Restart the bot. The **🤖 AI Support** button and `/ai` command appear automatically.

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
| `/restarttimer` | Shows the restart timer status (admins only) |
| `/admin` | Admin panel (admins only) |
| `/user <id>` | User details (admins only) |

---

## Hosting and restarts

Free hosting stops the bot after about 4 days, so it must be restarted by hand. The start time is saved in `data/settings.json`. Keep `data/` on persistent storage. If that folder is wiped on restart, the timer and user data are lost. Admins get a reminder 3 days after each start, and can also reset the timer from the admin panel.

---

## Data and secrets

- Never commit your bot token, provider API keys or a `.env` file.
- The `data/` folder holds real user records, balances and withdrawal details. Keep it out of version control. Add `data/` and `__pycache__/` to `.gitignore`.

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
