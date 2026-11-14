"""
otp_format.py - builds the "OTP Received" message a user gets when an SMS arrives.

    💲💲 OTP Received 💵💵

    ⏰ Time: 2026-10-05 08:14 PM
    📞 Number: 9779759235747
    🌐 Country: 🇳🇵 Nepal
    🔧 Service: 📘 Facebook

    🔑 OTP Code: 00000

    <#> 00000 is your Facebook code
    H29Q+Fsn4Sr

    💵 ৳0.40 has been added to your account.

Environment variables:
    OTP_TIMEZONE   optional, IANA name used for the "Time" line (default "Asia/Dhaka").
"""

from __future__ import annotations

import html
import os
import time
from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

# (dial code, ISO-3166 alpha-2, English name). Used for the flag + country name.
COUNTRIES: list[tuple[str, str, str]] = [
    ("1", "US", "United States"), ("7", "RU", "Russia"), ("20", "EG", "Egypt"), ("27", "ZA", "South Africa"),
    ("30", "GR", "Greece"), ("31", "NL", "Netherlands"), ("32", "BE", "Belgium"), ("33", "FR", "France"),
    ("34", "ES", "Spain"), ("36", "HU", "Hungary"), ("39", "IT", "Italy"), ("40", "RO", "Romania"),
    ("41", "CH", "Switzerland"), ("43", "AT", "Austria"), ("44", "GB", "United Kingdom"), ("45", "DK", "Denmark"),
    ("46", "SE", "Sweden"), ("47", "NO", "Norway"), ("48", "PL", "Poland"), ("49", "DE", "Germany"),
    ("51", "PE", "Peru"), ("52", "MX", "Mexico"), ("53", "CU", "Cuba"), ("54", "AR", "Argentina"),
    ("55", "BR", "Brazil"), ("56", "CL", "Chile"), ("57", "CO", "Colombia"), ("58", "VE", "Venezuela"),
    ("60", "MY", "Malaysia"), ("61", "AU", "Australia"), ("62", "ID", "Indonesia"), ("63", "PH", "Philippines"),
    ("64", "NZ", "New Zealand"), ("65", "SG", "Singapore"), ("66", "TH", "Thailand"), ("81", "JP", "Japan"),
    ("82", "KR", "South Korea"), ("84", "VN", "Vietnam"), ("86", "CN", "China"), ("90", "TR", "Turkey"),
    ("91", "IN", "India"), ("92", "PK", "Pakistan"), ("93", "AF", "Afghanistan"), ("94", "LK", "Sri Lanka"),
    ("95", "MM", "Myanmar"), ("98", "IR", "Iran"), ("211", "SS", "South Sudan"), ("212", "MA", "Morocco"),
    ("213", "DZ", "Algeria"), ("216", "TN", "Tunisia"), ("218", "LY", "Libya"), ("220", "GM", "Gambia"),
    ("221", "SN", "Senegal"), ("222", "MR", "Mauritania"), ("223", "ML", "Mali"), ("224", "GN", "Guinea"),
    ("225", "CI", "Ivory Coast"), ("226", "BF", "Burkina Faso"), ("227", "NE", "Niger"), ("228", "TG", "Togo"),
    ("229", "BJ", "Benin"), ("230", "MU", "Mauritius"), ("231", "LR", "Liberia"), ("232", "SL", "Sierra Leone"),
    ("233", "GH", "Ghana"), ("234", "NG", "Nigeria"), ("235", "TD", "Chad"), ("236", "CF", "Central African Republic"),
    ("237", "CM", "Cameroon"), ("238", "CV", "Cape Verde"), ("241", "GA", "Gabon"), ("242", "CG", "Congo"),
    ("243", "CD", "DR Congo"), ("244", "AO", "Angola"), ("249", "SD", "Sudan"), ("250", "RW", "Rwanda"),
    ("251", "ET", "Ethiopia"), ("252", "SO", "Somalia"), ("253", "DJ", "Djibouti"), ("254", "KE", "Kenya"),
    ("255", "TZ", "Tanzania"), ("256", "UG", "Uganda"), ("257", "BI", "Burundi"), ("258", "MZ", "Mozambique"),
    ("260", "ZM", "Zambia"), ("261", "MG", "Madagascar"), ("263", "ZW", "Zimbabwe"), ("264", "NA", "Namibia"),
    ("265", "MW", "Malawi"), ("266", "LS", "Lesotho"), ("267", "BW", "Botswana"), ("351", "PT", "Portugal"),
    ("352", "LU", "Luxembourg"), ("353", "IE", "Ireland"), ("354", "IS", "Iceland"), ("355", "AL", "Albania"),
    ("358", "FI", "Finland"), ("359", "BG", "Bulgaria"), ("370", "LT", "Lithuania"), ("371", "LV", "Latvia"),
    ("372", "EE", "Estonia"), ("373", "MD", "Moldova"), ("374", "AM", "Armenia"), ("375", "BY", "Belarus"),
    ("380", "UA", "Ukraine"), ("381", "RS", "Serbia"), ("385", "HR", "Croatia"), ("386", "SI", "Slovenia"),
    ("387", "BA", "Bosnia and Herzegovina"), ("420", "CZ", "Czech Republic"), ("421", "SK", "Slovakia"),
    ("501", "BZ", "Belize"), ("502", "GT", "Guatemala"), ("503", "SV", "El Salvador"), ("504", "HN", "Honduras"),
    ("505", "NI", "Nicaragua"), ("506", "CR", "Costa Rica"), ("507", "PA", "Panama"), ("509", "HT", "Haiti"),
    ("591", "BO", "Bolivia"), ("592", "GY", "Guyana"), ("593", "EC", "Ecuador"), ("595", "PY", "Paraguay"),
    ("597", "SR", "Suriname"), ("598", "UY", "Uruguay"), ("673", "BN", "Brunei"), ("852", "HK", "Hong Kong"),
    ("853", "MO", "Macau"), ("855", "KH", "Cambodia"), ("856", "LA", "Laos"), ("880", "BD", "Bangladesh"),
    ("886", "TW", "Taiwan"), ("960", "MV", "Maldives"), ("961", "LB", "Lebanon"), ("962", "JO", "Jordan"),
    ("963", "SY", "Syria"), ("964", "IQ", "Iraq"), ("965", "KW", "Kuwait"), ("966", "SA", "Saudi Arabia"),
    ("967", "YE", "Yemen"), ("968", "OM", "Oman"), ("971", "AE", "United Arab Emirates"), ("972", "IL", "Israel"),
    ("973", "BH", "Bahrain"), ("974", "QA", "Qatar"), ("975", "BT", "Bhutan"), ("976", "MN", "Mongolia"),
    ("977", "NP", "Nepal"), ("992", "TJ", "Tajikistan"), ("993", "TM", "Turkmenistan"), ("994", "AZ", "Azerbaijan"),
    ("995", "GE", "Georgia"), ("996", "KG", "Kyrgyzstan"), ("998", "UZ", "Uzbekistan"),
]

_BY_DIAL = {d: (iso, name) for d, iso, name in COUNTRIES}
_BY_NAME = {name.lower(): (iso, name) for _, iso, name in COUNTRIES}
_ALIASES = {  # provider spellings -> canonical name above
    "usa": "united states", "united states of america": "united states", "uk": "united kingdom",
    "england": "united kingdom", "uae": "united arab emirates", "cote d'ivoire": "ivory coast",
    "côte d'ivoire": "ivory coast", "czechia": "czech republic", "burma": "myanmar", "russian federation": "russia",
    "korea": "south korea", "republic of korea": "south korea", "viet nam": "vietnam", "dr congo": "dr congo",
    "democratic republic of the congo": "dr congo", "congo (kinshasa)": "dr congo",
}


def flag(iso: str) -> str:
    """'NP' -> 🇳🇵"""
    iso = (iso or "").upper()
    if len(iso) != 2 or not iso.isalpha():
        return "🌍"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso)


def _by_number(no_plus: str) -> Optional[tuple[str, str]]:
    digits = "".join(c for c in str(no_plus) if c.isdigit())
    for n in (3, 2, 1):                       # longest dial-code prefix wins
        hit = _BY_DIAL.get(digits[:n])
        if hit:
            return hit
    return None


def country_line(country: str, no_plus: str) -> str:
    """'🇳🇵 Nepal' - from the provider's country name if we know it, else from the number's dial code."""
    name = (country or "").strip()
    low = _ALIASES.get(name.lower(), name.lower())
    hit = _BY_NAME.get(low) if name else None
    if hit is None:
        hit = _by_number(no_plus)
    if hit is None:
        return f"🌍 {name}" if name else "🌍 Unknown"
    iso, canon = hit
    return f"{flag(iso)} {name or canon}"


def now_text(ts_ms: int = 0) -> str:
    """'2026-10-05 08:14 PM' in OTP_TIMEZONE (default Asia/Dhaka)."""
    ts = ts_ms / 1000 if ts_ms else time.time()
    tz = None
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(os.getenv("OTP_TIMEZONE") or "Asia/Dhaka")
        except Exception:
            tz = None
    dt = datetime.fromtimestamp(ts, tz) if tz else datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %I:%M %p")


def build_otp_message(*, number: str, country: str, service_emoji: str, service: str,
                      code: Optional[str], sms: str, reward: Optional[str], ts_ms: int = 0) -> str:
    """HTML text of the OTP message. `reward` like '৳0.40' (None -> the "added" line is left out)."""
    esc = html.escape
    lines = [
        "💲💲 <b>OTP Received</b> 💵💵", "",
        f"⏰ <b>Time:</b> {now_text(ts_ms)}",
        f"📞 <b>Number:</b> <code>{esc(number)}</code>",
        f"🌐 <b>Country:</b> {esc(country_line(country, number))}",
        f"🔧 <b>Service:</b> {esc(service_emoji)} {esc(service)}",
        "",
    ]
    if code:
        lines += [f"🔑 <b>OTP Code:</b> <code>{esc(code)}</code>", ""]
    lines.append(esc((sms or "").strip()))
    if reward:
        lines += ["", f"💵 <b>{esc(reward)}</b> has been added to your account."]
    return "\n".join(lines)
