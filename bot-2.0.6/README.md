# Nexora Bot 2.0.6

Version 2.0.6 adds **Get Number 3**: a third number source. The admin keeps a pool of numbers in Telegram, each number is given to one user only, and the SMS are read from the Lamix panel.

---

## What changed

- **Get Number 3** button in the main menu (shown only when `LAMIX_API_KEY` is set).
- **📱 Number pool** in the admin panel (`/admin` → 📱 Number pool):
  - **➕ Paste numbers**: send one number per line. Spaces, dashes and `+880` are fine. Duplicates are skipped.
  - **🗑 Remove numbers**: paste numbers to remove. Only free numbers are removed.
  - **🧹 Remove all free**: removes every free number, after a confirmation.
  - Numbers already given out are never removed, so running orders keep working.
- **SMS source**: the Lamix panel REST API (`GET /api/v1/messages`). One request covers all active numbers.
- **Reward**: ৳0.30 per SMS on Get Number 3 (`DEFAULT_PRICE`, 30 paisa).
- **Features page**: 2.0.6 is the current version.

---

## Setup

1. In the hosting **Environment Variables**, set:

   | Variable | Value |
   |---|---|
   | `LAMIX_API_KEY` | The API token from the Lamix panel's REST API page |

   Do not put the token in code, in a file, or in a chat message. Only the Environment.

2. Upload the bot files and restart.
3. In the admin panel, open 📱 Number pool and paste the numbers.

The numbers must be the same numbers the Lamix panel receives SMS on. If a number is not on the panel, no SMS will arrive for it.

---

## How it works

1. A user taps **Get Number 3**. The bot takes one free number and marks it as used at once.
2. The bot checks the Lamix panel every 5 seconds for up to 20 minutes.
3. Every new SMS on that number is delivered to that user, and the reward is added.
4. A number is never given to a second user.

### Rate limits

Lamix allows 1 request per second and 40 requests per minute for each endpoint. The bot waits 5 seconds between requests. If the panel returns `429`, the bot waits for the time the panel gives in `Retry-After`, then tries again.

### Errors the bot shows in the log

| Log text | Meaning |
|---|---|
| `lamix HTTP 401` | `LAMIX_API_KEY` is wrong or missing. Check the Environment. |
| `lamix rate limited; waiting ...` | The panel asked the bot to wait. This is normal for a short time. |
| `lamix HTTP 403` | The account or panel does not allow this request. Ask the Lamix provider. |
| `lamix HTTP ... not JSON` | The panel sent an unexpected reply. Share the log line with the provider. |

---

## Files changed in 2.0.6

| File | Change |
|---|---|
| `bot.py` | Number pool remove options, Get Number 3 menu, version 2.0.6 |
| `crapi_pool.py` | Lamix client, pool storage, remove and clear functions |
| `features_text.py` | 2.0.6 entry, marked as current |
| `tests/test_crapi_pool.py` | Pool, Lamix reply parsing, rate limit and key tests |
| `tests/test_features_text.py` | Current version is 2.0.6 |

---

## Tests

```bash
python -m pytest -q tests
```

All 117 tests pass.

---

## Upgrade notes

- Replace all bot files, or at least `bot.py`, `crapi_pool.py` and `features_text.py`. Upload `bot.py` and `web_admin.py` from the same zip. Mixing files from different versions causes startup errors.
- Old keys `CRAPI_TOKEN` and `CRAPI_API_KEY` are not used any more. Remove them if you like.
- Do not upload the `data/` folder. It holds the user database.
