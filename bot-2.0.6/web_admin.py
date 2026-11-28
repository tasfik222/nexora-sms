"""Password-protected admin website (2.0.2).

Pages:
  /                  users, balances, totals (search)
  /withdrawals       withdrawal requests by status
  /w/<id>            one request; pay or reject it
  /notes             admin notes; add a note

Access uses HTTP Basic auth with WEB_ADMIN_PASSWORD (at least 8 characters).
Paying or rejecting also needs the password typed again in the form, and every
action is written to the admin notes. Run behind HTTPS when the host gives it.
"""
from __future__ import annotations

import base64
import hmac
import html
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger("voltx.web")

MIN_PASSWORD = 8
MAX_BODY = 20000
MAX_FAILS = 5                 # wrong passwords allowed per IP ...
FAIL_WINDOW = 600             # ... within this many seconds
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def too_many_failures(ip: str, now: float) -> bool:
    with _fails_lock:
        hits = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        _fails[ip] = hits
        return len(hits) >= MAX_FAILS


def record_failure(ip: str, now: float) -> None:
    with _fails_lock:
        _fails.setdefault(ip, []).append(now)


def clear_failures(ip: str) -> None:
    with _fails_lock:
        _fails.pop(ip, None)


@dataclass
class Ops:
    """Functions the bot gives the website. They run the same code as the Telegram admin panel."""
    list_withdrawals: Callable[[str], list]           # status -> rows
    get_withdrawal: Callable[[int], Optional[dict]]
    decide: Callable[[int, str, str], tuple]          # (id, paid|rejected, reason) -> (row, error)
    add_note: Callable[[str], int]
    list_notes: Callable[[int, int], list]            # offset, limit
    count_notes: Callable[[], int]
    delete_note: Callable[[int], bool]


def build_rows(records: dict[int, dict]) -> list[dict]:
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


CSS = """
:root { --bg:#f4f1ff; --card:#fff; --text:#1f1b33; --muted:#6b6688; --line:#e7e3f5;
  --grad1:#7c3aed; --grad2:#ec4899; --grad3:#f59e0b; --ok:#059669; --ban:#dc2626; --flag:#d97706; --accent:#7c3aed; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0f0d1a; --card:#1a1730; --text:#ece9ff; --muted:#9b95c4; --line:#2a2650; } }
:root[data-theme="dark"] { --bg:#0f0d1a; --card:#1a1730; --text:#ece9ff; --muted:#9b95c4; --line:#2a2650; }
* { box-sizing:border-box; }
body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }
header { background:linear-gradient(135deg,var(--grad1),var(--grad2) 60%,var(--grad3)); color:#fff; padding:18px 16px 26px; }
header h1 { margin:0; font-size:21px; }
header .sub { opacity:.9; font-size:13px; margin-top:4px; }
nav { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
nav a { color:#fff; background:rgba(255,255,255,.2); padding:6px 12px; border-radius:999px; text-decoration:none; font-size:14px; }
nav a.on { background:#fff; color:var(--accent); font-weight:700; }
main { padding:0 12px 24px; margin-top:-14px; }
.card { background:var(--card); border-radius:14px; padding:14px; box-shadow:0 6px 18px rgba(80,60,160,.12); margin-bottom:12px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:12px; }
.stat { background:var(--card); border-radius:14px; padding:14px; box-shadow:0 6px 18px rgba(80,60,160,.12); border-top:4px solid var(--accent); }
.stat:nth-child(2) { border-top-color:var(--grad2); } .stat:nth-child(3) { border-top-color:var(--grad3); } .stat:nth-child(4) { border-top-color:var(--ban); }
.label { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
.value { font-size:22px; font-weight:700; margin-top:4px; }
input[type=search], input[type=text], input[type=password], textarea { width:100%; padding:11px 12px; border-radius:12px; border:2px solid var(--line); background:var(--card); color:var(--text); font-size:15px; }
textarea { min-height:90px; }
input:focus, textarea:focus { outline:none; border-color:var(--accent); }
.wrap { background:var(--card); border-radius:14px; overflow-x:auto; box-shadow:0 6px 18px rgba(80,60,160,.12); }
table { border-collapse:collapse; width:100%; min-width:600px; }
th,td { padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; font-size:14px; }
th { background:linear-gradient(90deg,rgba(124,58,237,.12),rgba(236,72,153,.12)); font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
tbody tr:hover { background:rgba(124,58,237,.06); }
a { color:var(--accent); }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.bal { font-weight:700; color:var(--ok); }
.mono { font-family:ui-monospace,Menlo,Consolas,monospace; color:var(--muted); }
.muted { color:var(--muted); font-size:13px; }
.pill { display:inline-block; padding:3px 9px; border-radius:999px; font-size:12px; font-weight:600; margin-right:4px; color:#fff; }
.ok-p { background:var(--ok); } .ban-p { background:var(--ban); } .flag-p { background:var(--flag); } .wait-p { background:var(--grad1); }
.btn { display:inline-block; border:0; border-radius:12px; padding:12px 16px; font-size:15px; font-weight:700; color:#fff; background:var(--accent); cursor:pointer; text-decoration:none; }
.btn.green { background:var(--ok); } .btn.red { background:var(--ban); }
.notice { padding:10px 12px; border-radius:12px; margin-bottom:12px; font-weight:600; }
.notice.good { background:rgba(5,150,105,.15); color:var(--ok); } .notice.bad { background:rgba(220,38,38,.12); color:var(--ban); }
.field { margin:10px 0; }
.label-s { display:block; font-size:13px; color:var(--muted); margin-bottom:4px; }
footer { text-align:center; color:var(--muted); font-size:12px; padding:14px; }
"""


def layout(title: str, active: str, body: str, generated: str = "") -> str:
    tabs = [("users", "/", "👥 Users"), ("withdrawals", "/withdrawals", "💸 Withdrawals"), ("notes", "/notes", "📝 Notes")]
    nav = "".join(f'<a class="{"on" if key == active else ""}" href="{href}">{label}</a>' for key, href, label in tabs)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{html.escape(title)}</title>
<style>{CSS}</style></head>
<body>
<header>
  <h1>✨ Nexora admin · {html.escape(title)}</h1>
  <div class="sub">{('Generated ' + html.escape(generated)) if generated else 'Private admin page'}</div>
  <nav>{nav}</nav>
</header>
<main>{body}</main>
<footer>Private admin page. Do not share the link or password.</footer>
</body></html>"""


def render_page(rows: Iterable[dict], generated_at: str) -> str:
    rows = list(rows)
    total_balance = sum(r["balance_taka"] for r in rows)
    total_codes = sum(r["codes"] for r in rows)
    flagged = sum(1 for r in rows if r["flagged"])
    body = []
    for r in rows:
        pills = []
        if r["banned"]:
            pills.append("<span class='pill ban-p'>banned</span>")
        if r["flagged"]:
            pills.append("<span class='pill flag-p'>flagged</span>")
        if not pills:
            pills.append("<span class='pill ok-p'>active</span>")
        uname = f"@{r['username']}" if r["username"] else "-"
        search_text = f"{r['uid']} {uname} {r['name']}".lower()
        body.append(
            f"<tr data-q='{html.escape(search_text, quote=True)}'>"
            f"<td class='mono'>{r['uid']}</td><td>{html.escape(uname)}</td><td>{html.escape(r['name'] or '-')}</td>"
            f"<td class='num bal'>৳{r['balance_taka']:.2f}</td><td class='num'>{r['codes']}</td>"
            f"<td>{''.join(pills)}</td><td class='muted'>{html.escape(r['last_seen'] or '-')}</td></tr>")
    content = f"""
<div class="stats">
  <div class="stat"><div class="label">Users</div><div class="value">{len(rows)}</div></div>
  <div class="stat"><div class="label">Total balance</div><div class="value">৳{total_balance:.2f}</div></div>
  <div class="stat"><div class="label">Total codes</div><div class="value">{total_codes}</div></div>
  <div class="stat"><div class="label">Flagged</div><div class="value">{flagged}</div></div>
</div>
<div class="card"><input id="search" type="search" placeholder="🔍 Search by ID, username or name" autocomplete="off">
<div class="muted" id="count" style="margin-top:6px">{len(rows)} shown</div></div>
<div class="wrap"><table>
<thead><tr><th>ID</th><th>Username</th><th>Name</th><th>Balance</th><th>Codes</th><th>Status</th><th>Last seen</th></tr></thead>
<tbody id="rows">{''.join(body)}</tbody></table></div>
<script>
(function () {{
  var box = document.getElementById('search');
  var rows = Array.prototype.slice.call(document.querySelectorAll('#rows tr'));
  var count = document.getElementById('count');
  box.addEventListener('input', function () {{
    var q = box.value.trim().toLowerCase().replace(/^@/, '');
    var shown = 0;
    rows.forEach(function (tr) {{
      var match = !q || tr.getAttribute('data-q').indexOf(q) !== -1;
      tr.style.display = match ? '' : 'none';
      if (match) shown++;
    }});
    count.textContent = shown + ' shown';
  }});
}})();
</script>"""
    return layout("Users", "users", content, generated_at)


STATUS_PILL = {"pending": "wait-p", "paid": "ok-p", "rejected": "ban-p", "cancelled": "flag-p"}


def _amt(r: dict) -> float:
    """Withdrawal amounts are stored as text such as "50.00" in some versions. Always show them as a number."""
    try:
        return float(r.get("amount_taka") or 0)
    except (TypeError, ValueError):
        return 0.0


def filter_withdrawals(rows: list[dict], query: str) -> list[dict]:
    """Match the query against the account number (full or part), user ID, request ID or amount."""
    q = (query or "").strip().lower()
    if not q:
        return rows
    qd = "".join(ch for ch in q if ch.isdigit())
    if qd.startswith("880"):                              # +880 country code: compare the local number
        qd = qd[3:]
    out = []
    for r in rows:
        account = str(r.get("account") or "")
        plain = q.replace(" ", "") in f"{account} {r.get('user_id', '')} {r.get('id', '')}".replace(" ", "").lower() \
            or q.replace(" ", "") in f"{_amt(r):.2f}"
        account_digits = "".join(ch for ch in account if ch.isdigit())
        if account_digits.startswith("880"):
            account_digits = account_digits[3:]
        digits_ok = len(qd) >= 4 and qd in account_digits
        if plain or digits_ok:
            out.append(r)
    return out


def render_withdrawals(rows: list[dict], status: str, query: str = "") -> str:
    tabs = "".join(
        f'<a class="btn {"green" if s == status else ""}" style="margin-right:6px;padding:8px 12px;font-size:13px" '
        f'href="/withdrawals?s={s}">{s.title()}</a>' for s in ("pending", "paid", "rejected"))
    search = (f"<form method='get' action='/withdrawals' class='card' style='display:flex;gap:8px;flex-wrap:wrap'>"
              f"<input type='hidden' name='s' value='{html.escape(status, quote=True)}'>"
              f"<input type='search' name='q' value='{html.escape(query, quote=True)}' "
              f"placeholder='🔍 Account number, user ID, request # or amount' style='flex:1 1 260px'>"
              f"<button class='btn' type='submit'>Search</button>"
              f"{'<a class=\"btn red\" href=\"/withdrawals?s=' + html.escape(status, quote=True) + '\">Clear</a>' if query else ''}"
              f"</form>")
    body_rows = []
    for r in rows:
        body_rows.append(
            f"<tr><td class='mono'>#{r['id']}</td><td class='num bal'>৳{_amt(r):.2f}</td>"
            f"<td>{html.escape(str(r.get('method') or '-'))}</td>"
            f"<td><span class='pill {STATUS_PILL.get(r['status'], 'flag-p')}'>{html.escape(r['status'])}</span></td>"
            f"<td class='muted'>{html.escape(str(r.get('created_at') or '')[:16].replace('T', ' '))}</td>"
            f"<td><a href='/w/{r['id']}'>Open →</a></td></tr>")
    table = ("<div class='wrap'><table><thead><tr><th>ID</th><th>Amount</th><th>Method</th><th>Status</th>"
             "<th>Created</th><th></th></tr></thead><tbody>" + "".join(body_rows) + "</tbody></table></div>"
             ) if rows else "<div class='card muted'>No requests found.</div>"
    return layout("Withdrawals", "withdrawals", f"<div class='card'>{tabs}</div>{search}{table}")


def render_withdrawal(r: dict, notice: str = "", notice_kind: str = "good", can_act: bool = True) -> str:
    pending = r["status"] == "pending" and can_act
    note = f"<div class='notice {notice_kind}'>{html.escape(notice)}</div>" if notice else ""
    info = (f"<div class='card'><div class='label'>Request</div><div class='value'>#{r['id']} · ৳{_amt(r):.2f}</div>"
            f"<div class='field'><span class='label-s'>Method</span>{html.escape(str(r.get('method') or '-'))}</div>"
            f"<div class='field'><span class='label-s'>Account number</span><code>{html.escape(str(r.get('account') or '-'))}</code></div>"
            f"<div class='field'><span class='label-s'>Status</span><span class='pill {STATUS_PILL.get(r['status'], 'flag-p')}'>{html.escape(r['status'])}</span></div>"
            f"<div class='field'><span class='label-s'>Created</span>{html.escape(str(r.get('created_at') or '-'))}</div>"
            f"<div class='field'><span class='label-s'>Reason</span>{html.escape(str(r.get('reason') or '-'))}</div></div>")
    if not can_act and r["status"] == "pending":
        actions = "<div class='card muted'>View only. Pay and reject need the admin password.</div>"
    elif pending:
        actions = f"""
<div class="card"><form method="post" action="/w/{r['id']}/decide">
  <input type="hidden" name="decision" value="paid">
  <div class="field"><span class="label-s">Type the admin password to confirm</span><input type="password" name="confirm_pw" required autocomplete="off"></div>
  <button class="btn green" type="submit" onclick="return confirm('Mark this as PAID?')">✅ Mark as paid</button>
</form></div>
<div class="card"><form method="post" action="/w/{r['id']}/decide">
  <input type="hidden" name="decision" value="rejected">
  <div class="field"><span class="label-s">Reason (the user sees it)</span><textarea name="reason" maxlength="200"></textarea></div>
  <div class="field"><span class="label-s">Type the admin password to confirm</span><input type="password" name="confirm_pw" required autocomplete="off"></div>
  <button class="btn red" type="submit" onclick="return confirm('Reject this and return the money?')">❌ Reject and return money</button>
</form></div>"""
    else:
        actions = "<div class='card muted'>This request is already closed.</div>"
    return layout(f"Request #{r['id']}", "withdrawals", note + info + actions)


def render_notes(rows: list[dict], total: int, page: int, pages: int, notice: str = "", can_act: bool = True) -> str:
    note = f"<div class='notice good'>{html.escape(notice)}</div>" if notice else ""
    form = """<div class="card"><form method="post" action="/notes">
<div class="field"><span class="label-s">New note</span><textarea name="text" maxlength="2000" required></textarea></div>
<button class="btn" type="submit">➕ Save note</button></form></div>"""
    items = []
    for r in rows:
        when = html.escape(r["created_at"].replace("T", " ")[:16])
        items.append(f"<div class='card'><div class='muted'>#{r['id']} · {when} UTC · <span class='mono'>{r['author_id']}</span></div>"
                     f"<div style='margin-top:6px;white-space:pre-wrap'>{html.escape(r['text'])}</div>"
                     f"<details style='margin-top:10px'><summary class='muted' style='cursor:pointer'>🗑 Delete this note</summary>"
                     f"<form method='post' action='/notes/{r['id']}/delete' style='margin-top:8px'>"
                     f"<div class='field'><span class='label-s'>Type the admin password to delete</span>"
                     f"<input type='password' name='confirm_pw' required autocomplete='off'></div>"
                     f"<button class='btn red' type='submit' onclick=\"return confirm('Delete this note? It cannot be undone.')\">Delete note</button>"
                     f"</form></details></div>")
    nav = ""
    if page > 0:
        nav += f"<a class='btn' style='margin-right:6px' href='/notes?p={page - 1}'>⬅️ Newer</a>"
    if page + 1 < pages:
        nav += f"<a class='btn' href='/notes?p={page + 1}'>Older ➡️</a>"
    if not can_act:
        form = "<div class='card muted'>View only. Adding and deleting notes needs the admin password.</div>"
        items = [re.sub(r"<details.*?</details>", "", it, flags=re.S) for it in items]
    body = note + form + f"<div class='muted'>{total} notes</div>" + "".join(items) + f"<div style='margin-top:10px'>{nav}</div>"
    return layout("Notes", "notes", body)


def check_basic_auth(header: Optional[str], expected_password: str) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    _, _, password = decoded.partition(":")
    return hmac.compare_digest(password.encode(), expected_password.encode())


def same_password(given: str, expected: str) -> bool:
    return hmac.compare_digest((given or "").encode(), expected.encode())


def make_handler(get_records, password: str, ops: Optional[Ops], viewer_password: str = ""):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NexoraAdmin"

        def log_message(self, fmt, *args):
            return

        def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8", headers: dict | None = None):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _deny(self):
            self._send(401, "Password required", "text/plain; charset=utf-8",
                       {"WWW-Authenticate": 'Basic realm="Nexora admin", charset="UTF-8"'})

        def _authed(self) -> bool:
            import time
            ip = self.client_address[0]
            now = time.monotonic()
            if too_many_failures(ip, now):
                self._send(429, "Too many wrong passwords. Try again in a few minutes.", "text/plain; charset=utf-8")
                return False
            header = self.headers.get("Authorization")
            if check_basic_auth(header, password):
                clear_failures(ip)
                self._role = "admin"
                return True
            if viewer_password and check_basic_auth(header, viewer_password):
                clear_failures(ip)
                self._role = "viewer"
                return True
            if header:
                record_failure(ip, now)
            self._deny()
            return False

        def _can_act(self) -> bool:
            return getattr(self, "_role", "") == "admin"

        def _form(self) -> dict:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

        def _redirect(self, location: str):
            self._send(303, "", headers={"Location": location})

        def do_GET(self):
            if not self._authed():
                return
            url = urlsplit(self.path)
            path, qs = url.path, parse_qs(url.query)
            try:
                if path in ("/", "/index.html"):
                    rows = build_rows(get_records())
                    self._send(200, render_page(rows, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))
                    return
                if ops is None:
                    self._send(404, "Not found")
                    return
                if path == "/withdrawals":
                    status = (qs.get("s") or ["pending"])[0]
                    if status not in ("pending", "paid", "rejected"):
                        status = "pending"
                    query = (qs.get("q") or [""])[0][:60]
                    rows = filter_withdrawals(ops.list_withdrawals(status), query)
                    self._send(200, render_withdrawals(rows, status, query))
                    return
                if path.startswith("/w/") and path.count("/") == 2:
                    rid = path[3:]
                    row = ops.get_withdrawal(int(rid)) if rid.isdigit() else None
                    if not row:
                        self._send(404, layout("Not found", "withdrawals", "<div class='card'>No such request.</div>"))
                        return
                    self._send(200, render_withdrawal(row, can_act=self._can_act()))
                    return
                if path == "/notes":
                    page = int((qs.get("p") or ["0"])[0]) if (qs.get("p") or ["0"])[0].isdigit() else 0
                    total = ops.count_notes()
                    pages = max(1, -(-total // 10))
                    page = min(page, pages - 1)
                    self._send(200, render_notes(ops.list_notes(page * 10, 10), total, page, pages, can_act=self._can_act()))
                    return
                self._send(404, "Not found")
            except Exception:
                log.exception("Admin web page failed")
                self._send(500, "Something went wrong.", "text/plain; charset=utf-8")

        def do_POST(self):
            if not self._authed():
                return
            if ops is None:
                self._send(404, "Not found")
                return
            path = urlsplit(self.path).path
            if not self._can_act():
                self._send(403, "View only. This password cannot change anything.", "text/plain; charset=utf-8")
                return
            try:
                form = self._form()
                if path == "/notes":
                    text = (form.get("text") or "").strip()
                    if not text:
                        self._send(200, render_notes(ops.list_notes(0, 10), ops.count_notes(), 0, 1, "Empty note, nothing saved."))
                        return
                    number = ops.add_note(text)
                    self._send(200, render_notes(ops.list_notes(0, 10), ops.count_notes(), 0, 1, f"Saved note #{number}."))
                    return
                if path.startswith("/notes/") and path.endswith("/delete"):
                    nid = path[len("/notes/"):-len("/delete")]
                    if not nid.isdigit():
                        self._send(400, "Bad request", "text/plain; charset=utf-8")
                        return
                    if not same_password(form.get("confirm_pw", ""), password):
                        self._send(403, render_notes(ops.list_notes(0, 10), ops.count_notes(), 0, 1, "Wrong password. Nothing deleted."))
                        return
                    removed = ops.delete_note(int(nid))
                    msg = f"Note #{nid} deleted." if removed else f"No note #{nid}. Nothing changed."
                    self._send(200, render_notes(ops.list_notes(0, 10), ops.count_notes(), 0, 1, msg))
                    return
                if path.startswith("/w/") and path.endswith("/decide"):
                    rid = path[3:-len("/decide")]
                    decision = form.get("decision", "")
                    if not rid.isdigit() or decision not in ("paid", "rejected"):
                        self._send(400, "Bad request", "text/plain; charset=utf-8")
                        return
                    row = ops.get_withdrawal(int(rid))
                    if not row:
                        self._send(404, layout("Not found", "withdrawals", "<div class='card'>No such request.</div>"))
                        return
                    if not same_password(form.get("confirm_pw", ""), password):
                        self._send(403, render_withdrawal(row, "Wrong password. Nothing changed.", "bad"))
                        return
                    if row["status"] != "pending":
                        self._send(200, render_withdrawal(row, "This request is already closed.", "bad"))
                        return
                    updated, err = ops.decide(int(rid), decision, (form.get("reason") or "").strip()[:200])
                    if err:
                        self._send(200, render_withdrawal(row, err, "bad"))
                        return
                    msg = "Marked as paid. The user was notified." if decision == "paid" else "Rejected. The money was returned to the user."
                    self._send(200, render_withdrawal(updated, msg))
                    return
                self._send(404, "Not found")
            except Exception:
                log.exception("Admin web action failed")
                self._send(500, "Something went wrong. Check the bot log.", "text/plain; charset=utf-8")

    return Handler


def start_server(get_records, password: str, port: int, host: str = "0.0.0.0",
                 ops: Optional[Ops] = None, viewer_password: str = "") -> ThreadingHTTPServer | None:
    """Start the website in a background thread. Returns None when the password is missing or too short."""
    if len(password or "") < MIN_PASSWORD:
        log.warning("Admin web page is OFF: set WEB_ADMIN_PASSWORD (at least %d characters).", MIN_PASSWORD)
        return None
    if viewer_password and (len(viewer_password) < MIN_PASSWORD or viewer_password == password):
        log.warning("Viewer password ignored: it must be at least %d characters and different from the admin password.", MIN_PASSWORD)
        viewer_password = ""
    server = ThreadingHTTPServer((host, port), make_handler(get_records, password, ops, viewer_password))
    threading.Thread(target=server.serve_forever, name="admin-web", daemon=True).start()
    log.info("Admin web page is on port %d (password protected).", port)
    return server


def port_from_env() -> int:
    import os
    return int(os.getenv("WEB_PORT") or os.getenv("PORT") or 8080)
