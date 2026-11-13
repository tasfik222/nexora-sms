"""
settings.py - small admin-controlled settings, saved in ./data/settings.json (LOCAL_DATA_DIR respected).

Settings:
    numbers_per_range   how many numbers a user gets at once after picking a range (1..MAX_NUMBERS).
                        Changed from the admin panel (Admin -> 🔢 Numbers per range).
    default_price       reward in PAISA for every SMS on a range that has no price of its own (default 30 = ৳0.30).
    range_prices        {range: paisa} - a different reward for a specific range.
                        Changed from the admin panel (Admin -> 💰 Range prices).
    range_names         {range: "display name"} - a custom name shown to users instead of the raw range.
                        Changed from the admin panel (Admin -> 🏷 Range names).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("voltx.settings")

MAX_NUMBERS = 10
DEFAULT_NUMBERS = 1
DEFAULT_PRICE = 30          # paisa -> ৳0.30 (same as the old fixed price)
MAX_NAME_LEN = 40
MAX_PRICE = 100_000         # paisa -> ৳1000 per SMS, just a safety cap against typos


def range_key(range_str: str) -> str:
    """'22501XXX' / ' 22501xxx ' / '+22501' -> '22501'  (one key per range, however it is written)."""
    return re.sub(r"[xX]+$", "", str(range_str).strip().lstrip("+"))


class SettingsStore:
    def __init__(self) -> None:
        self.path = Path(os.getenv("LOCAL_DATA_DIR") or "data") / "settings.json"
        self._data: dict = {"numbers_per_range": DEFAULT_NUMBERS, "default_price": DEFAULT_PRICE, "range_prices": {}, "range_names": {}}
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data["numbers_per_range"] = self._clamp(raw.get("numbers_per_range", DEFAULT_NUMBERS))
                self._data["default_price"] = self._price(raw.get("default_price"), DEFAULT_PRICE)
                prices = raw.get("range_prices") or {}
                self._data["range_prices"] = {
                    range_key(k): self._price(v, None) for k, v in prices.items()
                    if range_key(k) and self._price(v, None) is not None
                }
                names = raw.get("range_names") or {}
                self._data["range_names"] = {
                    range_key(k): self._name(v) for k, v in names.items()
                    if range_key(k) and self._name(v)
                }
        except (OSError, ValueError, AttributeError) as e:
            log.warning("Could not read %s (%s) - using defaults", self.path, e)

    @staticmethod
    def _clamp(value) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return DEFAULT_NUMBERS
        return max(1, min(MAX_NUMBERS, n))

    @staticmethod
    def _price(value, fallback: Optional[int]) -> Optional[int]:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return fallback
        return n if 0 <= n <= MAX_PRICE else fallback

    @staticmethod
    def _name(value) -> str:
        """Clean a display name: one line, trimmed, max MAX_NAME_LEN chars ('' = invalid)."""
        return " ".join(str(value or "").split())[:MAX_NAME_LEN]

    # -- numbers per range ----------------------------------------------------
    @property
    def numbers_per_range(self) -> int:
        return self._data["numbers_per_range"]

    def set_numbers_per_range(self, value) -> int:
        self._data["numbers_per_range"] = self._clamp(value)
        self._save()
        return self._data["numbers_per_range"]

    # -- prices ---------------------------------------------------------------
    @property
    def default_price(self) -> int:
        return self._data["default_price"]

    def set_default_price(self, paisa: int) -> bool:
        p = self._price(paisa, None)
        if p is None:
            return False
        self._data["default_price"] = p
        self._save()
        return True

    @property
    def range_prices(self) -> dict[str, int]:
        return dict(self._data["range_prices"])

    def has_custom_price(self, range_str: str) -> bool:
        return range_key(range_str) in self._data["range_prices"]

    def price_for(self, range_str: str) -> int:
        """Reward (paisa) for one SMS on this range: its own price, else the default."""
        return self._data["range_prices"].get(range_key(range_str), self._data["default_price"])

    def set_range_price(self, range_str: str, paisa: int) -> bool:
        key, p = range_key(range_str), self._price(paisa, None)
        if not key or p is None:
            return False
        self._data["range_prices"][key] = p
        self._save()
        return True

    def remove_range_price(self, range_str: str) -> bool:
        if self._data["range_prices"].pop(range_key(range_str), None) is None:
            return False
        self._save()
        return True

    # -- range names ------------------------------------------------------------
    @property
    def range_names(self) -> dict[str, str]:
        return dict(self._data["range_names"])

    def has_custom_name(self, range_str: str) -> bool:
        return range_key(range_str) in self._data["range_names"]

    def name_for(self, range_str: str) -> str:
        """The admin's custom name for this range, or '' when it has none."""
        return self._data["range_names"].get(range_key(range_str), "")

    def set_range_name(self, range_str: str, name: str) -> bool:
        key, nm = range_key(range_str), self._name(name)
        if not key or not nm:
            return False
        self._data["range_names"][key] = nm
        self._save()
        return True

    def remove_range_name(self, range_str: str) -> bool:
        if self._data["range_names"].pop(range_key(range_str), None) is None:
            return False
        self._save()
        return True

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.error("Could not save settings: %s", e)
