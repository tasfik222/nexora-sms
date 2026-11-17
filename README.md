# Nexora Telegram Bot

💬 **Discord:** Join my Discord community for communication, information, and support.

[![Discord](https://img.shields.io/badge/Discord-Join%20Community-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/8unNuUkyE9)

A Python Telegram bot that lets users get temporary phone numbers from SMS provider APIs, receive the incoming OTP/SMS right inside Telegram, and earn a small reward for every SMS received. It comes with a full admin panel, withdrawal requests, live traffic stats, backups and a built-in mini-game arena.

> **Latest version:** [`bot-0.12.0`](https://github.com/tasfik222/nexora-sms/tree/main/bot-0.12.0). Folders such as `files`, `files v2` ... `file v10 beta` are older releases kept for history.

---

## Features

**For users**
- **Get Number** - pick a server, a service (WhatsApp, Telegram, Facebook, ...) and a range, then receive number(s) instantly.
- **Automatic OTP delivery** - the bot checks for new SMS every 5 seconds for up to 20 minutes and sends each code to the user. Every new SMS on the number is rewarded, not just the first one.
- **Change Number** - get a new number from the same range with one tap.
- **Balance and history** - reward per SMS is stored in paisa (100 paisa = ৳1), so there are no floating-point errors.
- **Withdraw** - request a payout via bKash, Nagad or Rocket (minimum amount is configurable).
- **Top Traffic** - the busiest ranges of the last N minutes, tap one to order from it.
- **Console** - quick link to the live console group.
- **Leaderboard** - top 10 users by OTPs, daily (resets at 12:00 AM) and lifetime.
- **Arena (`/game`)** - mini-games: Quiz, Word Scramble, Code Breaker, Tic-Tac-Toe and Memory. Single, duo and squad modes, private rooms, XP, ELO and daily rewards. Supports English and Bangla. Game coins are separate from the money balance.

**For admins** (`/admin`)
- Review withdrawals: mark as paid or reject (the money is returned automatically).
- Set the **numbers per range**, the **default reward**, **per-range prices** and **custom range names**.
- **Stop / Start** the bot (maintenance mode with a notice to all users).
- User list with search, ban / unban, and per-user details (`/user <id>`).
- Post and pin a **group announcement** with an editable text.
- **Backups** - sent to a private chat and restorable by uploading the `.zip` to the bot.

**Other**
- Users must join a required group before they can use the bot.
- Optional channel feed that posts every new SMS (numbers are masked by default).
- Two SMS providers are supported (Voltx and Zenex). A provider without an API key is hidden automatically.

---

## Project structure (`file v11`)

| File | Purpose |
|---|---|
| `bot.py` | Main Telegram bot: menus, number flow, SMS polling, withdrawals, admin panel |
| `api_client.py` | Provider clients (Voltx, Zenex) with one common interface |
| `storage.py` | Local per-user records (balance, history, bans) with duplicate-credit protection |
| `withdrawals.py` | Withdrawal request storage and status handling |
| `settings.py` | Admin-controlled settings saved in `data/settings.json` |
| `traffic.py` | Top-traffic tracking from the live console |
| `console_api.py` | Reads live console entries from the providers |
| `channel_feed.py` | Optional channel feed of new SMS |
| `otp_format.py` | Formats the OTP messages sent to users |
| `backup.py` | Creates and restores data backups |
| `arena.py`, `arena_games.py`, `arena_quiz_data.py` | Mini-game arena |
| `tests/` | Tests |

The `app/` folder in the repository root is a small Next.js page used to serve a zipped download of one bot version.

---

## Requirements

- Python 3.10+
- [`python-telegram-bot`](https://python-telegram-bot.org) `>= 22.7`
- [`httpx`](https://www.python-httpx.org) `>= 0.25`

---

## Setup

```bash
git clone https://github.com/tasfik222/nexora-telegram-bot-python-code.git
cd nexora-telegram-bot-python-code/"file v11"

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Set the environment variables (see below), then start the bot:

```bash
python bot.py
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `VOLTXSMS_API_KEY` | one of the two | Voltx provider key |
| `ZENEX_API_KEY` | one of the two | Zenex provider key |
| `ADMIN_IDS` | recommended | Comma-separated Telegram user IDs of the admins. Without it, the admin panel falls back to the built-in default ID |
| `REQUIRED_GROUP_ID` | optional | Group users must join first. Use `off` to disable. The bot must be an admin of that group |
| `REQUIRED_GROUP_LINK` | optional | Invite link for the Join button |
| `CHANNEL_ID` | optional | Channel that receives every new SMS (bot must be channel admin) |
| `BACKUP_CHAT_ID` | optional | Private chat/channel where backups are sent and pinned |
| `CONSOLE_GROUP_LINK` | optional | Link opened by the Console button |
| `SUPPORT_LINK` | optional | Link opened by the Support button |
| `OTP_GROUP_LINK` | optional | Shows an "OTP Group" button |
| `WITHDRAW_MIN_TAKA` | optional | Minimum withdrawal in taka (default `50`) |
| `WITHDRAW_METHODS` | optional | Payout methods (default `bKash,Nagad,Rocket`) |
| `TRAFFIC_WINDOW_MIN` | optional | Minutes of history for Top Traffic (default `30`) |
| `FEED_MASK` | optional | Set to `0` to show full numbers in the console view and feed (masked by default) |
| `LOCAL_DATA_DIR` | optional | Where user data is saved (default `./data`) |

Example:

```bash
export TELEGRAM_BOT_TOKEN="123456:YOUR_TOKEN_HERE"
export ZENEX_API_KEY="your_key"
export ADMIN_IDS="123456789"
python bot.py
```

---

## How it works

1. The user taps **Get Number**, chooses a service and a range.
2. The bot orders number(s) from the selected provider.
3. It polls the provider's OTP endpoint every 5 seconds for up to 20 minutes.
4. Each new SMS is sent to the user and the range's reward is added to their balance (the same SMS is never paid twice).
5. When the balance reaches the minimum, the user can submit a withdrawal request, which an admin reviews in the admin panel.

---

## Data and secrets

- Never commit your bot token, provider API keys or a `.env` file.
- The `data/` folder holds real user records, balances and withdrawal details. Keep it out of version control (add `data/` and `__pycache__/` to `.gitignore`).

---

## Disclaimer

This project is intended for authorized SMS verification workflows only. Use it only with services, numbers and accounts you own or are explicitly allowed to use, and follow the terms of your SMS providers and Telegram.

---

## License

Released under the [MIT License](./LICENSE). Copyright (c) 2026 Tasfik Abdullah.

## Author

**Tasfik Abdullah** - [@tasfik222](https://github.com/tasfik222)

💬 **Discord:** Join my Discord community for communication, information, and support.

[![Discord](https://img.shields.io/badge/Discord-Join%20Community-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/8unNuUkyE9)
