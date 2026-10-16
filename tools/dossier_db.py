"""Database and workflow rules for Dossier Compleet (SQLite, standard library only).

Holds the state (clients, requests, uploads, events, mails) and every rule that changes it:
starting a quarter, recording a verdict, a human review, and reminders.
A demo clock (settings.clock_days) lets the demo jump forward in time without waiting real days.
"""

import json
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta

import send_mail
from check_document import parse_date
from dossier_common import CLIENTS, DOSSIER, OFFICE_NAME, QUARTER, REQUIRED_ITEMS

DB = DOSSIER / "dossier.db"
UPLOADS = DOSSIER / "uploads"
REMINDER_DAYS = (3, 7, 14)  # reminder 1, 2, 3; after the third one the office calls

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, name TEXT, business TEXT, email TEXT, address TEXT, story TEXT);
CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, client_id TEXT, quarter TEXT, token TEXT UNIQUE,
    sent_at TEXT, completed_at TEXT, reminders INTEGER DEFAULT 0, call_needed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS uploads (id INTEGER PRIMARY KEY, request_id INTEGER, slot TEXT, original_slot TEXT,
    filename TEXT, path TEXT, uploaded_at TEXT, status TEXT, reason TEXT, client_msg TEXT, office_msg TEXT,
    fields TEXT, model TEXT, seconds REAL, decided_by TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, request_id INTEGER, at TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS mails (id INTEGER PRIMARY KEY, request_id INTEGER, at TEXT, kind TEXT, to_addr TEXT, subject TEXT);
"""

_lock = threading.RLock()
_conn = None


def db():
    global _conn
    if _conn is None:
        DOSSIER.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        if not _conn.execute("SELECT 1 FROM clients").fetchone():
            _conn.executemany("INSERT INTO clients VALUES (?,?,?,?,?,?)",
                              [(c["id"], c["name"], c["business"], c["email"], c["address"], c["story"]) for c in CLIENTS])
            _conn.commit()
    return _conn


def q(sql, *args):
    with _lock:
        return [dict(r) for r in db().execute(sql, args).fetchall()]


def x(sql, *args):
    with _lock:
        cur = db().execute(sql, args)
        db().commit()
        return cur.lastrowid


# ---------- clock ----------

def clock_days():
    rows = q("SELECT value FROM settings WHERE key='clock_days'")
    return int(rows[0]["value"]) if rows else 0


def now():
    return (datetime.now() + timedelta(days=clock_days())).replace(microsecond=0)


def iso():
    return now().isoformat()


# ---------- setup ----------

def reset():
    """Wipe all demo state and seed the fictional clients."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        DB.unlink(missing_ok=True)
        if UPLOADS.exists():
            for f in sorted(UPLOADS.rglob("*"), reverse=True):
                f.unlink() if f.is_file() else f.rmdir()
        db()  # recreates the schema and seeds only explicit mock clients


def client(cid):
    return q("SELECT * FROM clients WHERE id=?", cid)[0]


def event(request_id, kind, detail):
    x("INSERT INTO events (request_id, at, kind, detail) VALUES (?,?,?,?)", request_id, iso(), kind, detail)


def mail(request_id, kind, to, subject):
    x("INSERT INTO mails (request_id, at, kind, to_addr, subject) VALUES (?,?,?,?,?)", request_id, iso(), kind, to, subject)


def portal_link(base_url, token):
    return f"{base_url}/u/{token}"


# ---------- workflow ----------

def start_quarter(base_url):
    """Create a request per client for this quarter and mail them their personal upload link."""
    labels = [label for label, _ in REQUIRED_ITEMS.values()]
    started = 0
    for c in q("SELECT * FROM clients ORDER BY rowid"):
        if q("SELECT 1 FROM requests WHERE client_id=? AND quarter=?", c["id"], QUARTER):
            continue
        token = secrets.token_urlsafe(9)
        rid = x("INSERT INTO requests (client_id, quarter, token, sent_at) VALUES (?,?,?,?)", c["id"], QUARTER, token, iso())
        subject = send_mail.request_mail(c, portal_link(base_url, token), labels)
        mail(rid, "verzoek", c["email"], subject)
        event(rid, "verzoek", f"Uitvraag Q3 gemaild naar {c['business']}")
        started += 1
    return started


def request_by_token(token):
    rows = q("SELECT * FROM requests WHERE token=?", token)
    return rows[0] if rows else None


def slot_states(request_id):
    """Per slot: overall status + its uploads (newest first)."""
    uploads = q("SELECT * FROM uploads WHERE request_id=? ORDER BY id DESC", request_id)
    out = {}
    for slot, (label, _) in REQUIRED_ITEMS.items():
        mine = [u for u in uploads if u["slot"] == slot]
        statuses = [u["status"] for u in mine]
        if "goedgekeurd" in statuses:
            status = "goedgekeurd"
        elif "controleren" in statuses:
            status = "controleren"
        elif "twijfel" in statuses:
            status = "twijfel"
        elif statuses:
            status = statuses[0]  # newest upload decides: afgewezen
        else:
            status = "ontbreekt"
        out[slot] = {"label": label, "status": status, "uploads": mine}
    return out


def add_upload(request, slot, filename, data):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[-80:] or "document"
    uid = x("INSERT INTO uploads (request_id, slot, original_slot, filename, uploaded_at, status) VALUES (?,?,?,?,?,?)",
            request["id"], slot, slot, filename, iso(), "controleren")
    folder = UPLOADS / str(request["id"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uid}_{safe}"
    path.write_bytes(data)
    x("UPDATE uploads SET path=? WHERE id=?", str(path), uid)
    c = client(request["client_id"])
    event(request["id"], "upload", f"{c['business']} uploadde '{filename}' bij {REQUIRED_ITEMS[slot][0].split(' (')[0]}")
    return uid


def upload(uid):
    return q("SELECT * FROM uploads WHERE id=?", uid)[0]


def request_of(upload_row):
    return q("SELECT * FROM requests WHERE id=?", upload_row["request_id"])[0]


def apply_check(uid, result, base_url):
    """Store the AI verdict and do what follows from it: mail the client, alert the office, close the file."""
    u = upload(uid)
    r = request_of(u)
    c = client(r["client_id"])
    x("""UPDATE uploads SET slot=?, status=?, reason=?, client_msg=?, office_msg=?, fields=?, model=?, seconds=?, decided_by=?
         WHERE id=?""", result["slot"], result["verdict"], result["reason"], result["client_msg"], result["office_msg"],
      json.dumps(result.get("fields", {}), ensure_ascii=False), result.get("model"), result.get("seconds"),
      "demo-regels" if result.get("model") == "demo" else "AI", uid)
    secs = f" ({result.get('seconds')} s)" if result.get("seconds") else ""
    words = {"goedgekeurd": "keurde goed", "afgewezen": "wees af", "twijfel": "twijfelt over"}[result["verdict"]]
    event(r["id"], "ai_" + result["verdict"], f"{'Demo-regels' if result.get('model') == 'demo' else 'AI'} {words}: '{u['filename']}' van {c['business']}{secs}")

    link = portal_link(base_url, r["token"])
    if result["verdict"] == "afgewezen":
        subject = send_mail.rejected_mail(c, link, REQUIRED_ITEMS[result["slot"]][0], result["client_msg"])
        mail(r["id"], "afwijzing", c["email"], subject)
    elif result["verdict"] == "twijfel":
        subject = send_mail.office_review_mail(c, u["filename"], result["office_msg"], f"{base_url}/kantoor")
        mail(r["id"], "kantoor", send_mail.OFFICE, subject)
    maybe_complete(r["id"])


def review(uid, decision, base_url, note=""):
    """A human decides on a 'twijfel' document."""
    u = upload(uid)
    r = request_of(u)
    c = client(r["client_id"])
    msg = note or ("Goedgekeurd door een medewerker." if decision == "goedgekeurd"
                   else "Een medewerker heeft dit document bekeken en kan het niet gebruiken. Wil je het juiste document uploaden?")
    x("UPDATE uploads SET status=?, client_msg=?, decided_by='kantoor' WHERE id=?", decision, msg, uid)
    event(r["id"], "kantoor_" + decision, f"Kantoor {'keurde goed' if decision == 'goedgekeurd' else 'wees af'}: "
                                          f"'{u['filename']}' van {c['business']}")
    if decision == "afgewezen":
        subject = send_mail.rejected_mail(c, portal_link(base_url, r["token"]), REQUIRED_ITEMS[u["slot"]][0], msg)
        mail(r["id"], "afwijzing", c["email"], subject)
    maybe_complete(r["id"])


def maybe_complete(request_id):
    r = q("SELECT * FROM requests WHERE id=?", request_id)[0]
    if r["completed_at"] or any(s["status"] != "goedgekeurd" for s in slot_states(request_id).values()):
        return False
    c = client(r["client_id"])
    x("UPDATE requests SET completed_at=?, call_needed=0 WHERE id=?", iso(), request_id)
    subject = send_mail.complete_mail(c)
    mail(request_id, "compleet", c["email"], subject)
    event(request_id, "compleet", f"Dossier compleet: {c['business']}")
    return True


def run_reminders(base_url):
    """Send the reminder that is due for every open request; after the last one, flag 'bellen'."""
    actions = []
    for r in q("SELECT * FROM requests WHERE completed_at IS NULL AND sent_at IS NOT NULL"):
        states = slot_states(r["id"])
        missing = [s["label"] for s in states.values() if s["status"] in ("ontbreekt", "afgewezen")]
        if not missing:
            continue  # the client did their part; the office is reviewing
        days = (now() - datetime.fromisoformat(r["sent_at"])).days
        due = sum(days >= d for d in REMINDER_DAYS)
        if due <= r["reminders"]:
            continue
        c = client(r["client_id"])
        subject = send_mail.reminder_mail(c, portal_link(base_url, r["token"]), due, missing)
        mail(r["id"], "herinnering", c["email"], subject)
        x("UPDATE requests SET reminders=? WHERE id=?", due, r["id"])
        event(r["id"], "herinnering", f"Herinnering {due} gemaild naar {c['business']} (dag {days})")
        actions.append(f"herinnering {due} -> {c['business']}")
        if due == len(REMINDER_DAYS):
            x("UPDATE requests SET call_needed=1 WHERE id=?", r["id"])
            event(r["id"], "bellen", f"Bel {c['name']} ({c['business']}): na 3 herinneringen nog niet compleet")
            actions.append(f"bellen -> {c['business']}")
    return actions


def advance_clock(days, base_url):
    x("INSERT INTO settings VALUES ('clock_days', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      str(clock_days() + days))
    event(None, "klok", f"Demo: {days} dagen vooruit naar {now():%d-%m-%Y}")
    return run_reminders(base_url)


# ---------- reporting ----------

def named(f):
    """Name lookup on what the AI read: the client's own spelling where the name belongs to the client
    (check_document.known_name), otherwise exactly what was printed."""
    return lambda key: (f.get("herkend") or {}).get(key) or f.get(key) or ""


def reading(u):
    """What the AI read from an upload, trimmed to what a person wants to see (for the portal and dashboard)."""
    f = json.loads(u.get("fields") or "{}")
    if not f:
        return None
    start, end = parse_date(f.get("datum_van")), parse_date(f.get("datum_tot"))
    start, end = start or end, end or start
    soort = {"bankafschrift": "Bankafschrift", "factuur": "Factuur", "kassabon": "Kassabon", "overig": "Ander document"}
    name = named(f)
    return {"soort": soort.get(f.get("documentsoort"), "Document"),
            "naam": name("rekeninghouder") or name("afzender"),
            "aan": name("geadresseerde"),
            "van": start.isoformat() if start else None, "tot": end.isoformat() if end else None,
            "totaal": f.get("totaal"), "btw": f.get("btw"), "scherpte": f.get("scherpte")}


def export_rows():
    rows = q("""SELECT c.business, u.slot, u.filename, u.fields, u.decided_by FROM uploads u
                JOIN requests r ON r.id=u.request_id JOIN clients c ON c.id=r.client_id
                WHERE u.status='goedgekeurd' ORDER BY c.business, u.slot""")
    def money(v):  # Dutch Excel: comma as decimal separator (the CSV uses ';' between columns)
        return f"{v:.2f}".replace(".", ",") if isinstance(v, (int, float)) else ""

    out = []
    for r in rows:
        f = json.loads(r["fields"] or "{}")
        start, end = parse_date(f.get("datum_van")), parse_date(f.get("datum_tot"))
        start, end = start or end, end or start
        bank, name = r["slot"] == "bank", named(f)
        out.append({"klant": r["business"], "soort": REQUIRED_ITEMS[r["slot"]][0].split(" (")[0].replace(" Q3 2026", ""),
                    "bestand": r["filename"], "van": name("afzender"),
                    "aan": name("rekeninghouder") if bank else name("geadresseerde"),
                    "datum": f"{start:%d-%m-%Y} t/m {end:%d-%m-%Y}" if bank and start else (f"{start:%d-%m-%Y}" if start else ""),
                    "totaal_incl_btw": "" if bank else money(f.get("totaal")), "btw": "" if bank else money(f.get("btw")),
                    "gecontroleerd_door": r["decided_by"]})
    return out


def dashboard():
    reqs = {r["client_id"]: r for r in q("SELECT * FROM requests WHERE quarter=?", QUARTER)}
    clients, review = [], []
    for c in q("SELECT * FROM clients ORDER BY rowid"):
        r = reqs.get(c["id"])
        entry = {"id": c["id"], "name": c["name"], "business": c["business"], "story": c["story"], "request": None}
        if r:
            states = slot_states(r["id"])
            for slot, s in states.items():
                for u in s["uploads"]:
                    if u["status"] == "twijfel":
                        review.append({"upload_id": u["id"], "business": c["business"], "name": c["name"],
                                       "filename": u["filename"], "slot_label": s["label"], "office_msg": u["office_msg"],
                                       "read": reading(u)})
                s["uploads"] = [{**{k: u[k] for k in ("id", "filename", "status", "reason", "decided_by", "seconds", "client_msg")},
                                 "read": reading(u)} for u in s["uploads"]]
            if r["completed_at"]:
                status = "compleet"
            elif any(s["status"] == "twijfel" for s in states.values()):
                status = "beoordelen"
            elif r["call_needed"]:
                status = "bellen"
            elif any(s["status"] == "controleren" for s in states.values()):
                status = "controleren"
            else:
                status = "wacht"
            days = (now() - datetime.fromisoformat(r["sent_at"])).days
            entry["request"] = {"token": r["token"], "days_open": days, "reminders": r["reminders"], "sent_at": r["sent_at"],
                                "status": status, "slots": states, "completed_at": r["completed_at"]}
        clients.append(entry)
    events = q("SELECT at, kind, detail FROM events ORDER BY id DESC LIMIT 40")
    statuses = [c["request"]["status"] for c in clients if c["request"]]
    return {"office": OFFICE_NAME, "quarter": QUARTER, "today": now().date().isoformat(), "clock_days": clock_days(),
            "started": bool(reqs), "clients": clients, "review": review, "events": events,
            "kpi": {"compleet": statuses.count("compleet"), "totaal": len(clients),
                    "wacht": statuses.count("wacht") + statuses.count("controleren"),
                    "beoordelen": len(review), "bellen": statuses.count("bellen"),
                    "mails": q("SELECT COUNT(*) n FROM mails")[0]["n"]},
            "demo": True}
