"""Group post for a paid withdrawal (2.0.0).

Shows the payout without any personal name or username. The account number
keeps only its first 4 digits; the rest is hidden.
"""
from __future__ import annotations

from datetime import datetime, timezone


def mask_account(account: str, keep: int = 4) -> str:
    """Keep the first `keep` characters and hide the rest with dots."""
    acc = (account or "").strip()
    if len(acc) <= keep:
        return acc
    return acc[:keep] + "•" * (len(acc) - keep)


def payout_text(req: dict, amount_text: str) -> str:
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        "✅ <b>Withdrawal paid</b>\n"
        f"Request: #{req.get('id')}\n"
        f"Amount: {amount_text}\n"
        f"Method: {req.get('method') or '-'}\n"
        f"Account: <code>{mask_account(str(req.get('account') or ''))}</code>\n"
        f"Time: {when}"
    )
