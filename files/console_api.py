"""
console_api.py - reads the sites' LIVE CONSOLE feed (the same data the website's console page shows:
number range, country, operator, service, masked message).

    Zenex : GET https://zenexnetwork.com/api/live-console          (public, no key needed)
    Voltx : GET https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api/console   (official, global live feed,
            sent with your VOLTXSMS_API_KEY in the `mauthapi` header; rows: range, sid, message, time)

Environment variables (all optional):
    ZENEX_CONSOLE_API      override the Zenex console URL
    VOLTX_CONSOLE_API      override the Voltx console URL
    VOLTX_CONSOLE_HEADER   extra header for that URL, as "Name: value"
    ZENEX_CONSOLE_HEADER   same for Zenex

If a site has no console URL, its regular OTP API (success-otp / numsuccess) is used instead.
The parser is tolerant: it finds the list under logs/data/items/messages/... and the fields
number/otp/message/country/operator/service/createdAt under their common names.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from api_client import VOLTX_AUTH_HEADER, ApiError, Provider

log = logging.getLogger("voltx.console")

DEFAULT_URLS = {
    "zenex": "https://zenexnetwork.com/api/live-console",
    "voltx": "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api/console",
}
URL_ENV = {"voltx": "VOLTX_CONSOLE_API", "zenex": "ZENEX_CONSOLE_API"}
BROWSER_HEADERS: dict[str, dict] = {}   # per-site extra headers, if a site ever needs them
HEADER_ENV = {"voltx": "VOLTX_CONSOLE_HEADER", "zenex": "ZENEX_CONSOLE_HEADER"}

LIST_KEYS = ("logs", "hits", "data", "items", "messages", "sms", "otps", "records", "list", "results")


@dataclass
class ConsoleEntry:
    entry_id: str
    number: str
    text: str
    country: str = ""
    operator: str = ""
    service: str = ""
    time: int = 0          # unix ms, 0 = unknown


def _pick(d: dict, *names: str) -> Any:
    for n in names:
        v = d.get(n)
        if v not in (None, ""):
            return v
    return None


def _ts_ms(v: Any) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 0
    return n * 1000 if 0 < n < 100_000_000_000 else n   # seconds -> ms


def _find_list(data: Any, depth: int = 0) -> list | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and depth < 2:
        for k in LIST_KEYS:
            if k in data:
                found = _find_list(data[k], depth + 1)
                if found is not None:
                    return found
    return None


def parse_console(data: Any) -> list[ConsoleEntry]:
    items = _find_list(data)
    if items is None:
        raise ValueError("no list found in console response")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        number = str(_pick(it, "number", "phone", "msisdn", "num", "range") or "").lstrip("+")
        text = str(_pick(it, "otp", "message", "sms", "text", "msg", "body") or "")
        ts = _ts_ms(_pick(it, "createdAt", "created_at", "time", "timestamp", "date"))
        eid = str(_pick(it, "id", "_id", "nid", "otp_id") or f"{number}|{ts}|{text}")
        out.append(ConsoleEntry(
            entry_id=eid, number=number, text=text, time=ts,
            country=str(_pick(it, "country", "country_name") or ""),
            operator=str(_pick(it, "operator", "carrier", "network") or ""),
            service=str(_pick(it, "service", "sid", "app", "platform") or ""),
        ))
    if any(e.time for e in out):
        out.sort(key=lambda e: e.time, reverse=True)
    return out


class ConsoleClient:
    def __init__(self, providers: dict[str, Provider]) -> None:
        self.providers = providers
        self._http = httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                       headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})

    async def close(self) -> None:
        await self._http.aclose()

    def url(self, key: str) -> str:
        return (os.getenv(URL_ENV.get(key, "")) or DEFAULT_URLS.get(key, "")).strip()

    def available(self, key: str) -> bool:
        if self.url(key):
            return True
        p = self.providers.get(key)
        return bool(p and p.enabled)

    def _headers(self, key: str) -> dict:
        return {**BROWSER_HEADERS.get(key, {}), **self._auth_headers(key)}

    def _auth_headers(self, key: str) -> dict:
        raw = (os.getenv(HEADER_ENV.get(key, "")) or "").strip()
        if ":" in raw:                                     # explicit header, e.g. "Mauth: <dashboard token>"
            name, value = raw.split(":", 1)
            return {name.strip(): value.strip()}
        prov = self.providers.get(key)
        if key == "voltx" and prov and prov.enabled:
            return {VOLTX_AUTH_HEADER: prov.api_key}       # fallback: the normal API key
        return {}

    async def fetch(self, key: str) -> list[ConsoleEntry]:
        url = self.url(key)
        if url:
            return await self._fetch_console(key, url)
        # fallback: the account's own OTP list from the normal API
        prov = self.providers.get(key)
        if not prov or not prov.enabled:
            raise ApiError("This console is not available.")
        return [ConsoleEntry(entry_id=o.otp_id, number=o.number, text=o.message, time=o.time)
                for o in await prov.get_otps()]

    async def _fetch_console(self, key: str, url: str) -> list[ConsoleEntry]:
        try:
            params = {"t": int(time.time() * 1000)}
            resp = await self._http.get(url, params=params, headers=self._headers(key))
            if resp.status_code == 405:                      # endpoint wants POST
                resp = await self._http.post(url, params=params, headers=self._headers(key))
        except httpx.TimeoutException:
            raise ApiError("The console took too long to respond. Please try again.")
        except httpx.HTTPError as e:
            raise ApiError("Could not reach the console. Please try again later.",
                           log_message=f"console HTTP error {key}: {e!r}")
        if resp.status_code in (401, 403):
            raise ApiError("The console rejected the request (check the API key / header).",
                           log_message=f"console {key} HTTP {resp.status_code} - key/header rejected")
        if resp.status_code >= 400:
            raise ApiError(f"The console returned an error (HTTP {resp.status_code}).",
                           log_message=f"console {key} HTTP {resp.status_code}: {resp.text[:80]!r}")
        try:
            return parse_console(resp.json())
        except (ValueError, TypeError) as e:
            hint = ""
            if not resp.text.lstrip().startswith(("{", "[")):
                hint = " (response is not JSON - site may be hiding data from bots or encoding it)"
            raise ApiError("The console returned an unexpected response.",
                           log_message=f"console {key} parse failed{hint}: {e!r} body={resp.text[:60]!r}")
