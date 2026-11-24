"""Parsing for /addbalance (2.0.0). Pure functions, no Telegram here."""
from __future__ import annotations

MAX_MANUAL_TAKA = 5000


def parse_addbalance(args: list[str]) -> tuple[int, int, str]:
    """Return (user_id, paisa, reason). Raises ValueError with a message for the admin."""
    if len(args) < 3:
        raise ValueError("Usage: /addbalance <user id> <taka> <reason>")
    if not args[0].isdigit():
        raise ValueError("The user id must be a number.")
    try:
        taka = float(args[1])
    except ValueError:
        raise ValueError("The amount must be a number of taka, for example 50.")
    if taka <= 0 or taka > MAX_MANUAL_TAKA:
        raise ValueError(f"The amount must be between 0 and {MAX_MANUAL_TAKA} taka.")
    paisa = round(taka * 100)
    reason = " ".join(args[2:]).strip()[:200]
    return int(args[0]), paisa, reason
