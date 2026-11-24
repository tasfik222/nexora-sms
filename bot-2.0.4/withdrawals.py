"""
withdrawals.py - withdrawal requests, saved as ./data/withdrawals.json (previous version kept as .bak).

Life of a request
    pending  -> paid        admin pressed "Mark paid" (money already sent by hand)
    pending  -> rejected    admin rejected it -> the amount goes back to the user's balance
    pending  -> cancelled   the user cancelled it -> the amount goes back to the user's balance

The amount is taken from the balance the moment the request is created, so a user can never withdraw the
same money twice. Every change below is synchronous (no await), so two taps can never both succeed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from storage import StorageError, UserStore, _now, _taka

log = logging.getLogger("voltx.withdraw")

PENDING, PAID, REJECTED, CANCELLED = "pending", "paid", "rejected", "cancelled"
STATUSES = (PENDING, PAID, REJECTED, CANCELLED)


class WithdrawError(Exception):
    """Something the user / admin should be told (safe to show)."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class WithdrawStore:
    def __init__(self, users: UserStore) -> None:
        self.users = users
        self.path = Path(os.getenv("LOCAL_DATA_DIR") or "data") / "withdrawals.json"
        self.bak = self.path.with_name(self.path.name + ".bak")
        self.reqs: dict[int, dict] = {}
        self.next_id = 1
        self.changes = 0

    # ------------------------------------------------------------------ load / save
    def load(self) -> None:
        for p in (self.path, self.bak):
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                self.reqs = {int(r["id"]): r for r in data.get("requests", [])}
                self.next_id = max(int(data.get("next_id", 1)), max(self.reqs, default=0) + 1)
                if p is self.bak:
                    log.warning("withdrawals.json was unreadable - loaded the .bak copy instead.")
                return
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.error("Could not read %s: %s", p, e)
        if self.path.exists() or self.bak.exists():
            raise StorageError("withdrawals.json is unreadable and there is no usable .bak copy")

    def export(self) -> dict:
        return {"next_id": self.next_id, "requests": sorted(self.reqs.values(), key=lambda r: r["id"])}

    def _save(self) -> None:
        self.changes += 1
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.export(), ensure_ascii=False, indent=2), encoding="utf-8")
            if self.path.exists():
                shutil.copyfile(self.path, self.bak)
            os.replace(tmp, self.path)
        except OSError as e:
            log.error("Could not write withdrawals.json: %s", e)

    # ------------------------------------------------------------------ queries
    def get(self, req_id: int) -> Optional[dict]:
        return self.reqs.get(req_id)

    def pending_for(self, uid: int) -> Optional[dict]:
        return next((r for r in self.reqs.values() if r["user_id"] == uid and r["status"] == PENDING), None)

    def list(self, status: str | None = None, uid: int | None = None, newest_first: bool = True,
             limit: int | None = None) -> list[dict]:
        out = [r for r in self.reqs.values()
               if (status is None or r["status"] == status) and (uid is None or r["user_id"] == uid)]
        out.sort(key=lambda r: r["id"], reverse=newest_first)
        return out[:limit] if limit else out

    def stats(self) -> dict:
        res = {s: {"count": 0, "amount": 0} for s in STATUSES}
        for r in self.reqs.values():
            res[r["status"]]["count"] += 1
            res[r["status"]]["amount"] += r["amount"]
        return res

    # ------------------------------------------------------------------ changes
    def create(self, user_id: int, username: str, full_name: str, method: str, account: str,
               amount: int, min_amount: int) -> dict:
        if self.pending_for(user_id):
            raise WithdrawError("You already have a withdrawal waiting for review.")
        if amount < min_amount:
            raise WithdrawError(f"The minimum withdrawal is ৳{_taka(min_amount)}.")
        if self.users.debit(user_id, amount) is None:
            raise WithdrawError("Your balance is too low for this amount.")
        now = _now()
        rec = {
            "id": self.next_id, "user_id": user_id, "username": username or "", "full_name": full_name or "",
            "method": method, "account": account, "amount": amount, "amount_taka": _taka(amount),
            "status": PENDING, "created_at": now, "updated_at": now,
            "decided_by": None, "decided_at": None, "reason": "",
        }
        self.next_id += 1
        self.reqs[rec["id"]] = rec
        self._save()
        return rec

    def _close(self, req_id: int, status: str, by: int, reason: str, owner: int | None = None) -> dict:
        r = self.reqs.get(req_id)
        if not r or (owner is not None and r["user_id"] != owner):
            raise WithdrawError("Request not found.")
        if r["status"] != PENDING:
            raise WithdrawError(f"This request is already {r['status']}.")
        if status in (REJECTED, CANCELLED):
            if self.users.refund(r["user_id"], r["amount"]) is None:
                raise WithdrawError("Could not return the money (user record not loaded). Please try again.")
        now = _now()
        r.update(status=status, decided_by=by, decided_at=now, updated_at=now, reason=reason)
        self._save()
        return r

    def mark_paid(self, req_id: int, admin_id: int) -> dict:
        return self._close(req_id, PAID, admin_id, "")

    def reject(self, req_id: int, admin_id: int, reason: str = "") -> dict:
        return self._close(req_id, REJECTED, admin_id, reason)

    def cancel(self, req_id: int, uid: int) -> dict:
        return self._close(req_id, CANCELLED, uid, "Cancelled by user", owner=uid)

    # ------------------------------------------------------------------ restore
    def merge_record(self, r: dict) -> bool:
        """Merge one request from a backup: the one with the later `updated_at` wins."""
        try:
            rid, uid = int(r["id"]), int(r["user_id"])
            if r["status"] not in STATUSES or type(r["amount"]) is not int or r["amount"] <= 0:
                return False
            r["id"], r["user_id"] = rid, uid
            r.setdefault("updated_at", r.get("created_at", ""))
        except (KeyError, TypeError, ValueError):
            return False
        cur = self.reqs.get(rid)
        if cur is not None and str(cur.get("updated_at", "")) >= str(r["updated_at"]):
            return False
        self.reqs[rid] = r
        self.next_id = max(self.next_id, rid + 1)
        self._save()
        return True

    def bump_next_id(self, value: int) -> None:
        if value > self.next_id:
            self.next_id = value
            self._save()
