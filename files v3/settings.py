"""
settings.py - small admin-controlled settings, saved in ./data/settings.json (LOCAL_DATA_DIR respected).

Currently one setting:
    numbers_per_range   how many numbers a user gets at once after picking a range (1..MAX_NUMBERS).
                        Changed from the admin panel (Admin -> 🔢 Numbers per range).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("voltx.settings")

MAX_NUMBERS = 10
DEFAULT_NUMBERS = 1


class SettingsStore:
    def __init__(self) -> None:
        self.path = Path(os.getenv("LOCAL_DATA_DIR") or "data") / "settings.json"
        self._data: dict = {"numbers_per_range": DEFAULT_NUMBERS}
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data["numbers_per_range"] = self._clamp(raw.get("numbers_per_range", DEFAULT_NUMBERS))
        except (OSError, ValueError, AttributeError) as e:
            log.warning("Could not read %s (%s) - using defaults", self.path, e)

    @staticmethod
    def _clamp(value) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return DEFAULT_NUMBERS
        return max(1, min(MAX_NUMBERS, n))

    @property
    def numbers_per_range(self) -> int:
        return self._data["numbers_per_range"]

    def set_numbers_per_range(self, value) -> int:
        self._data["numbers_per_range"] = self._clamp(value)
        self._save()
        return self._data["numbers_per_range"]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.error("Could not save settings: %s", e)
