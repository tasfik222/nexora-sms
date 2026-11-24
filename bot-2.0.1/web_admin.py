"""Password-protected admin web page (2.0.0).

Shows a table of users: Telegram ID, username, name, balance, codes, banned and
flagged state. Withdrawal details are never shown. The page only starts when
WEB_ADMIN_PASSWORD is set (at least 8 characters). Access uses HTTP Basic auth.

Run it behind HTTPS when the host provides it, because Basic auth sends the
password with every request.
"""
from __future__ import annotations

import base64
import hmac
import html
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable

log = logging.getLogger("voltx.web")

MIN_PASSWORD = 8


def build_rows(records: dict[int, dict]) -> list[dict]:
    """Turn user records into display rows. Only safe fields are copied."""
    rows = []
    for uid, rec in records.items():
        rows.append({
            "uid": int(uid),
            "username": str(rec.get("username") or ""),
            "name": str(rec.get("first_name") or ""),
            "balance_taka": int(rec.get("balance") or 0) / 100,
            "codes": int(rec.get("total_codes") or 0),
            "banned": bool(rec.get("banned")),
            "flagged": bool(rec.get("flagged")),
            "last_seen": str(rec.get("last_seen") or ""),
        })
    rows.sort(key=lambda r: r["balance_taka"], reverse=True)
    return rows


def render_page(rows: Iterable[dict], generated_at: str) -> str:
    rows = list(rows)
    total_balance = sum(r["balance_taka"] for r in rows)
    total_codes = sum(r["codes"] for r in rows)
    body = []
    for r in rows:
        status = []
        if r["banned"]:
            status.append("banned")
        if r["flagged"]:
            status.append("flagged")
        uname = f"@{r['username']}" if r["username"] else "-"
        body.append(
            "<tr>"
            f"<td>{r['uid']}</td>"
            f"<td>{html.escape(uname)}</td>"
            f"<td>{html.escape(r['name'] or '-')}</td>"
            f"<td class='num'>৳{r['balance_taka']:.2f}</td>"
            f"<td class='num'>{r['codes']}</td>"
            f"<td>{html.escape(', '.join(status) or 'ok')}</td>"
            f"<td>{html.escape(r['last_seen'] or '-')}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Nexora admin</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 16px; background: #f6f7f9; color: #111; }}
h1 {{ font-size: 20px; margin: 0 0 8px; }}
.summary {{ margin-bottom: 12px; color: #444; }}
.wrap {{ overflow-x: auto; background: #fff; border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; min-width: 620px; }}
th, td {{ padding: 8px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; font-size: 14px; }}
th {{ background: #f0f2f5; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #111318; color: #e6e6e6; }}
  .summary {{ color: #aaa; }}
  .wrap {{ background: #1a1d24; }}
  th {{ background: #232731; }}
  th, td {{ border-color: #2c3038; }}
}}
</style></head>
<body>
<h1>Nexora admin · users</h1>
<div class="summary">{len(rows)} users · total balance ৳{total_balance:.2f} · total codes {total_codes} · generated {html.escape(generated_at)}</div>
<div class="wrap"><table>
<thead><tr><th>ID</th><th>Username</th><th>Name</th><th>Balance</th><th>Codes</th><th>Status</th><th>Last seen</th></tr></thead>
<tbody>{''.join(body)}</tbody>
</table></div>
</body></html>"""


def check_basic_auth(header: str | None, expected_password: str) -> bool:
    """True when the Authorization header carries the right password (any user name)."""
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    _, _, password = decoded.partition(":")
    return hmac.compare_digest(password.encode(), expected_password.encode())


def make_handler(get_records, password: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NexoraAdmin"

        def log_message(self, fmt, *args):  # keep request lines out of the bot log
            return

        def _deny(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Nexora admin", charset="UTF-8"')
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Password required".encode())

        def do_GET(self):
            if self.path.split("?")[0] not in ("/", "/index.html"):
                self.send_response(404)
                self.end_headers()
                return
            if not check_basic_auth(self.headers.get("Authorization"), password):
                self._deny()
                return
            try:
                rows = build_rows(get_records())
                page = render_page(rows, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
                data = page.encode("utf-8")
            except Exception:
                log.exception("Admin web page failed")
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def start_server(get_records, password: str, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer | None:
    """Start the page in a background thread. Returns None when the password is missing or too short."""
    if len(password or "") < MIN_PASSWORD:
        log.warning("Admin web page is OFF: set WEB_ADMIN_PASSWORD (at least %d characters).", MIN_PASSWORD)
        return None
    server = ThreadingHTTPServer((host, port), make_handler(get_records, password))
    threading.Thread(target=server.serve_forever, name="admin-web", daemon=True).start()
    log.info("Admin web page is on port %d (password protected).", port)
    return server


def port_from_env() -> int:
    return int(os.getenv("WEB_PORT") or os.getenv("PORT") or 8080)
