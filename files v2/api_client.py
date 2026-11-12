"""
api_client.py - everything that talks to the SMS providers. Two sites are supported:

    1. "voltx" -> the 2oo9 / voltxsms token API      (key env: VOLTXSMS_API_KEY, header `mauthapi`)
    2. "zenex" -> the Zenex Network core API          (key env: ZENEX_API_KEY,    header `mapikey`)

Both providers expose the SAME three methods, so bot.py does not care which one it talks to:
    await provider.get_services()        -> list[Service]   (service -> ranges)
    await provider.get_number(range_str) -> NumberOrder     (range_str exactly as listed, e.g. "22501XXX")
    await provider.get_otps()            -> list[Otp]       (the account's recent OTPs)

A provider whose API key env var is empty is simply hidden from the bot's menu.
If an endpoint or JSON shape changes, edit that provider's CONFIG block and PARSERS only.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("voltx.api")

REQUEST_TIMEOUT = 20.0

# Lower-case substrings of error messages that mean "key problem" (both providers).
KEYWORDS_BAD_KEY = ("api key", "api_key", "apikey", "mauthapi", "mapikey", "unauthor", "authenticat",
                    "invalid key", "forbidden", "api access", "not enabled")

# =============================================================================
# CONFIG - provider 1: Voltx / 2oo9
# =============================================================================
VOLTX_BASE_URL = "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
VOLTX_API_KEY_ENV = "VOLTXSMS_API_KEY"
VOLTX_AUTH_HEADER = "mauthapi"
VOLTX_GETNUM = ("POST", "/getnum")            # body {"rid": "26134"}  (range WITHOUT the XXX)
VOLTX_LIVEACCESS = ("GET", "/liveaccess")     # services + ranges (cached 60s)
VOLTX_SUCCESS_OTP = ("GET", "/success-otp")   # your last 50 OTPs (cached 5s)
VOLTX_OUT_OF_STOCK_CODE = 2946

# =============================================================================
# CONFIG - provider 2: Zenex
# =============================================================================
ZENEX_BASE_URL = "https://api.zenexnetwork.com"
ZENEX_API_KEY_ENV = "ZENEX_API_KEY"
ZENEX_AUTH_HEADER = "mapikey"
ZENEX_GETNUM = ("POST", "/v1/getnum")                # body {"range": "4473845XXX", "is_national": false, "remove_plus": false}
ZENEX_OTPS = ("GET", "/v1/numsuccess/info")          # cache-free, poll every 3-5s
ZENEX_RANGES = ("GET", "/v1/active-ranges")          # 60s cache, poll every 2-5 min
# The Zenex docs don't list an "out of stock" code, so these message words are used instead.
ZENEX_KEYWORDS_NO_NUMBERS = ("no number", "not available", "unavailable", "out of stock", "no stock",
                             "no free", "not found", "no_stock", "exhausted")


# =============================================================================
# DATA
# =============================================================================
@dataclass
class Service:
    sid: str
    last_at: int
    ranges: list[str] = field(default_factory=list)   # e.g. ["22501XXX", "8801XXX"]
    labels: list[str] = field(default_factory=list)   # optional button text per range (same order)


@dataclass
class NumberOrder:
    full_number: str       # "+447404333228" (for display)
    no_plus_number: str    # "447404333228"  (matches `number` in the OTP list)
    country: str = ""
    operator: str = ""


@dataclass
class Otp:
    otp_id: str            # unique per SMS
    number: str            # no plus sign
    message: str
    time: int              # unix ms, or 0 if the provider gives no usable timestamp


def range_to_rid(range_str: str) -> str:
    """'26134XXX' -> '26134'  (Voltx getnum wants the range WITHOUT the trailing XXX)."""
    return re.sub(r"[xX]+$", "", range_str.strip())


_CODE_RE = re.compile(r"\b\d{3}[- ]\d{3}\b|\b\d{4,8}\b")


def extract_code(message: str) -> str | None:
    """Best-effort: pull a 4-8 digit (or 123-456 style) code out of the SMS text."""
    m = _CODE_RE.search(message or "")
    return re.sub(r"[- ]", "", m.group(0)) if m else None


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# =============================================================================
# ERRORS
# =============================================================================
class ApiError(Exception):
    """Any API failure. `user_message` is safe to show in Telegram."""

    def __init__(self, user_message: str, *, log_message: str | None = None):
        super().__init__(log_message or user_message)
        self.user_message = user_message


class ApiKeyError(ApiError):
    pass


class NoNumbersError(ApiError):
    pass


# =============================================================================
# PARSERS - Voltx
# =============================================================================
def voltx_parse_services(data: Any) -> list[Service]:
    items = (data or {}).get("services", [])
    out = [Service(sid=str(s.get("sid", "?")), last_at=_int(s.get("last_at")),
                   ranges=[str(r) for r in s.get("ranges", [])]) for s in items]
    out = [s for s in out if s.ranges]
    out.sort(key=lambda s: s.last_at, reverse=True)
    return out


def voltx_parse_number(data: Any) -> NumberOrder:
    full = str(data["full_number"])
    return NumberOrder(full_number=full, no_plus_number=str(data.get("no_plus_number") or full.lstrip("+")),
                       country=str(data.get("country") or ""), operator=str(data.get("operator") or ""))


def voltx_parse_otps(data: Any) -> list[Otp]:
    return [Otp(otp_id=str(o.get("otp_id", "")), number=str(o.get("number", "")).lstrip("+"),
                message=str(o.get("message", "")), time=_int(o.get("time")))
            for o in (data or {}).get("otps", [])]


# =============================================================================
# PARSERS - Zenex
# =============================================================================
def zenex_parse_services(data: Any) -> list[Service]:
    """active_ranges is a flat list of {range, service, tag, hits}; group it by service."""
    grouped: dict[str, dict] = {}
    for it in (data or {}).get("active_ranges", []):
        rng = str(it.get("range") or "").strip()
        if not rng:
            continue
        g = grouped.setdefault(str(it.get("service") or "Other"), {"hits": 0, "ranges": {}})
        hits = _int(it.get("hits"))
        g["hits"] += hits
        prev = g["ranges"].get(rng)
        if prev is None or hits > prev[0]:
            g["ranges"][rng] = (hits, str(it.get("tag") or ""))
    out = []
    for sid, g in sorted(grouped.items(), key=lambda kv: kv[1]["hits"], reverse=True):
        rs = sorted(g["ranges"].items(), key=lambda kv: kv[1][0], reverse=True)
        out.append(Service(
            sid=sid, last_at=0, ranges=[r for r, _ in rs],
            labels=[f"{r} ({tag}, {hits})" if tag else f"{r} ({hits})" for r, (hits, tag) in rs],
        ))
    return out


def zenex_parse_number(data: Any) -> NumberOrder:
    display = str(data.get("number") or data.get("copy") or "")
    no_plus = str(data.get("full_number") or display).lstrip("+")
    if not no_plus:
        raise KeyError("number")
    return NumberOrder(full_number="+" + no_plus, no_plus_number=no_plus,
                       country=str(data.get("country") or ""), operator=str(data.get("operator") or ""))


def zenex_parse_otps(data: Any) -> list[Otp]:
    """Multi-OTP: each code is its own object with a unique `nid` (..._0, ..._1)."""
    out = []
    for o in (data or {}).get("otps", []):
        nid = str(o.get("nid") or "")
        if not nid:
            continue
        # created_at has no timezone in the docs, so time=0 ("unknown"); new-OTP detection uses `nid`.
        out.append(Otp(otp_id=nid, number=str(o.get("number", "")).lstrip("+"),
                       message=str(o.get("otp", "")), time=0))
    return out


# =============================================================================
# PROVIDERS
# =============================================================================
class Provider:
    key = ""
    label = ""
    api_key_env = ""

    def __init__(self) -> None:
        self.api_key = (os.getenv(self.api_key_env) or "").strip()
        self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def close(self) -> None:
        await self._http.aclose()

    def _require_key(self) -> None:
        if not self.api_key:
            log.error("%s is not set.", self.api_key_env)
            raise ApiKeyError("This server has no API key configured yet. Please contact the bot admin.",
                              log_message=f"{self.api_key_env} missing")

    async def _send(self, method: str, url: str, headers: dict, json_body: dict | None) -> tuple[httpx.Response, Any]:
        try:
            resp = await self._http.request(method, url, json=json_body, headers=headers)
        except httpx.TimeoutException:
            raise ApiError("The SMS service took too long to respond. Please try again.")
        except httpx.HTTPError as e:
            raise ApiError("Could not reach the SMS service. Please try again later.",
                           log_message=f"HTTP error on {method} {url}: {e!r}")
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        return resp, payload

    # interface (implemented by subclasses)
    async def get_services(self) -> list[Service]: raise NotImplementedError
    async def get_number(self, range_str: str) -> NumberOrder: raise NotImplementedError
    async def get_otps(self) -> list[Otp]: raise NotImplementedError


class VoltxClient(Provider):
    key = "voltx"
    label = "Voltx"
    api_key_env = VOLTX_API_KEY_ENV

    async def get_services(self) -> list[Service]:
        services = voltx_parse_services(await self._request(*VOLTX_LIVEACCESS))
        if not services:
            raise NoNumbersError("No recently active services/ranges right now. Please try again shortly.")
        return services

    async def get_number(self, range_str: str) -> NumberOrder:
        data = await self._request(*VOLTX_GETNUM, json_body={"rid": range_to_rid(range_str)})
        try:
            return voltx_parse_number(data)
        except (KeyError, TypeError, AttributeError) as e:
            raise ApiError("The service returned an unexpected response when allocating a number.",
                           log_message=f"voltx parse_number failed: {e!r} data={data!r}")

    async def get_otps(self) -> list[Otp]:
        data = await self._request(*VOLTX_SUCCESS_OTP)
        try:
            return voltx_parse_otps(data)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ApiError("The service returned an unexpected OTP list.",
                           log_message=f"voltx parse_otps failed: {e!r} data={data!r}")

    async def _request(self, method: str, path: str, *, json_body: dict | None = None) -> Any:
        self._require_key()
        url = VOLTX_BASE_URL.rstrip("/") + path
        resp, payload = await self._send(method, url, {VOLTX_AUTH_HEADER: self.api_key}, json_body)

        meta = payload.get("meta") if isinstance(payload, dict) else None
        if not isinstance(meta, dict):
            if resp.status_code in (401, 403):
                log.error("Voltx key rejected (HTTP %s).", resp.status_code)
                raise ApiKeyError("The API key was rejected. Make sure API access is enabled for your account.")
            raise ApiError("The SMS service returned an unexpected (non-standard) response.",
                           log_message=f"Bad envelope from {method} {url} [{resp.status_code}]: {resp.text[:200]!r}")

        code, status = meta.get("code"), str(meta.get("status", ""))
        if code == 200:
            return payload.get("data")

        message = str(payload.get("message") or "")
        low = message.lower()
        log.warning("Voltx error code=%s status=%s http=%s msg=%s", code, status, resp.status_code, message)
        if code == VOLTX_OUT_OF_STOCK_CODE or status == "not_found":
            raise NoNumbersError("No numbers are available in that range right now. Try another range.")
        if resp.status_code in (401, 403) or code in (401, 403) or any(k in low for k in KEYWORDS_BAD_KEY):
            raise ApiKeyError("The API key was rejected. Make sure API access is enabled for your account.")
        if resp.status_code == 429 or code == 429:
            raise ApiError("Too many requests. Please wait a moment and try again.")
        raise ApiError(f"The service returned an error: {message or f'code {code}'}")


class ZenexClient(Provider):
    key = "zenex"
    label = "Zenex"
    api_key_env = ZENEX_API_KEY_ENV

    async def get_services(self) -> list[Service]:
        services = zenex_parse_services(await self._request(*ZENEX_RANGES))
        if not services:
            raise NoNumbersError("No active ranges right now. Please try again shortly.")
        return services

    async def get_number(self, range_str: str) -> NumberOrder:
        # Zenex wants the range WITH the XXX suffix, exactly as listed in active-ranges.
        data = await self._request(*ZENEX_GETNUM, json_body={
            "range": range_str.strip(), "is_national": False, "remove_plus": False})
        try:
            return zenex_parse_number(data)
        except (KeyError, TypeError, AttributeError) as e:
            raise ApiError("The service returned an unexpected response when allocating a number.",
                           log_message=f"zenex parse_number failed: {e!r} data={data!r}")

    async def get_otps(self) -> list[Otp]:
        data = await self._request(*ZENEX_OTPS)
        try:
            return zenex_parse_otps(data)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ApiError("The service returned an unexpected OTP list.",
                           log_message=f"zenex parse_otps failed: {e!r} data={data!r}")

    async def _request(self, method: str, path: str, *, json_body: dict | None = None) -> Any:
        self._require_key()
        url = ZENEX_BASE_URL.rstrip("/") + path
        resp, payload = await self._send(method, url, {ZENEX_AUTH_HEADER: self.api_key}, json_body)

        if not isinstance(payload, dict):
            if resp.status_code in (401, 403):
                log.error("Zenex key rejected (HTTP %s).", resp.status_code)
                raise ApiKeyError("The API key was rejected by this server. Please contact the bot admin.")
            raise ApiError("The SMS service returned an unexpected (non-JSON) response.",
                           log_message=f"Non-JSON from {method} {url} [{resp.status_code}]: {resp.text[:200]!r}")

        # Two success shapes: {"meta": {"code": 200}} (getnum / numsuccess) and {"success": true} (active-ranges).
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else None
        ok = (meta.get("code") == 200) if meta is not None else (payload.get("success") is True)
        if ok and resp.status_code < 400:
            return payload.get("data")

        code = meta.get("code") if meta else None
        status = str(meta.get("status", "")) if meta else ""
        message = str(payload.get("message") or payload.get("error") or "")
        low = message.lower()
        log.warning("Zenex error code=%s status=%s http=%s msg=%s", code, status, resp.status_code, message)

        if resp.status_code in (401, 403) or code in (401, 403) or any(k in low for k in KEYWORDS_BAD_KEY):
            log.error("Zenex key problem: %s", message)
            raise ApiKeyError("The API key was rejected by this server. Please contact the bot admin.")
        if resp.status_code == 429 or code == 429:
            raise ApiError("Too many requests. Please wait a moment and try again.")
        if status in ("not_found", "no_stock") or any(k in low for k in ZENEX_KEYWORDS_NO_NUMBERS):
            raise NoNumbersError("No numbers are available in that range right now. Try another range.")
        raise ApiError(f"The service returned an error: {message or f'code {code or resp.status_code}'}")


def build_providers() -> dict[str, Provider]:
    """All known providers, keyed by provider.key. Providers without an API key report enabled=False."""
    return {p.key: p for p in (VoltxClient(), ZenexClient())}
