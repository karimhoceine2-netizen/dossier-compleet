"""Local web server for Dossier Compleet: the client upload portal (/u/<token>) and the office dashboard (/kantoor).

Uploads are checked one at a time by a background worker (the vision model uses most of the Mac's memory).

    python3 tools/portal_server.py [--port 8787] [--model qwen2.5vl:7b]
"""

import argparse
import csv
import html
from email import policy
from email.parser import BytesParser
import io
import json
import queue
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import check_document
import dossier_db as dbx
from common import http
from dossier_common import DEMO_DOCS, MANIFEST, QUARTER, REQUIRED_ITEMS, DOSSIER

WEB = Path(__file__).parent / "dossier_web"
MAX_UPLOAD = 20 * 1024 * 1024
HINTS = {
    "bank": "Download het afschrift als PDF in je bank-app of via internetbankieren.",
    "verkoop": "Alle facturen die je zelf hebt verstuurd in juli, augustus en september.",
    "inkoop": "Facturen en bonnen van je zakelijke uitgaven. Een foto van een bon is prima.",
}

jobs = queue.Queue()
state = {"model": check_document.DEFAULT_MODEL, "base_url": "", "busy": None}


def worker():
    while True:
        uid = jobs.get()
        try:
            u = dbx.upload(uid)
            r = dbx.request_of(u)
            c = dbx.client(r["client_id"])
            state["busy"] = uid
            result = check_document.check(u["path"], u["slot"], c, state["model"])
            if result["cached"]:
                time.sleep(1.5)  # a reading from the cache returns instantly; keep the "checking" moment visible
            dbx.apply_check(uid, result, state["base_url"])
        except Exception as e:  # never lose an upload: a failed check goes to a human
            traceback.print_exc()
            dbx.apply_check(uid, {"verdict": "twijfel", "reason": "controle_mislukt", "slot": dbx.upload(uid)["slot"],
                                  "client_msg": "Dank je! Een medewerker van het kantoor bekijkt dit document.",
                                  "office_msg": "De controle is mislukt. Graag handmatig beoordelen."},
                            state["base_url"])
        finally:
            state["busy"] = None
            jobs.task_done()


def warm_up():
    """Load the model into memory, so the first real check is not 20 seconds slower."""
    http(f"{check_document.OLLAMA}/api/generate", method="POST",
         data={"model": state["model"], "prompt": "", "keep_alive": "30m"}, timeout=300)


def client_view(token):
    r = dbx.request_by_token(token)
    if not r:
        return None
    c = dbx.client(r["client_id"])
    slots = []
    for slot, s in dbx.slot_states(r["id"]).items():
        slots.append({"slot": slot, "label": s["label"].split(" (")[0], "period": "1 juli t/m 30 september 2026",
                      "hint": HINTS[slot], "status": s["status"],
                      "uploads": [{"filename": u["filename"], "status": u["status"], "message": u["client_msg"] or "",
                                   "read": dbx.reading(u)} for u in s["uploads"]]})
    return {"first_name": c["name"].split()[0], "business": c["business"], "office": dbx.OFFICE_NAME,
            "quarter": QUARTER, "deadline": "31 oktober 2026", "today": dbx.now().date().isoformat(),
            "complete": bool(r["completed_at"]), "slots": slots}


def demo_uploads(round_no):
    """Simulate the demo clients uploading their documents (round 1: first try, round 2: corrections)."""
    n = 0
    for d in json.loads(MANIFEST.read_text()):
        if d["upload_round"] != round_no:
            continue
        r = dbx.q("SELECT * FROM requests WHERE client_id=? AND quarter=?", d["client"], QUARTER)
        path = DEMO_DOCS / d["file"]
        if not r or dbx.q("SELECT 1 FROM uploads WHERE request_id=? AND filename=?", r[0]["id"], path.name):
            continue  # not started, or already uploaded by hand during the demo
        jobs.put(dbx.add_upload(r[0], d["slot"], path.name, path.read_bytes()))
        n += 1
    return n


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def page(self, name):
        self.send(200, (WEB / name).read_text(), "text/html; charset=utf-8")

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 <= n <= 4096:
            raise ValueError("Request is too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        parts = url.path.strip("/").split("/")
        if url.path in ("/", ""):
            return self.send(302, "", headers={"Location": "/kantoor"})
        if url.path == "/outbox":
            outbox = DOSSIER / "outbox"
            rows = []
            for file in sorted(outbox.glob("*.eml"), key=lambda f: f.stat().st_mtime, reverse=True):
                message = BytesParser(policy=policy.default).parsebytes(file.read_bytes())
                rows.append(f'<li><a href="/outbox/{file.stem}">{html.escape(str(message["Subject"]))}</a><p>{html.escape(str(message["To"]))}</p></li>')
            page = '<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Lokale demo-mailbox</title><link rel="stylesheet" href="/static/style.css"><main style="max-width:760px;margin:40px auto;padding:24px"><a href="/kantoor">← Dashboard</a><h1>Lokale demo-mailbox</h1><p>Geen berichten verzonden. Alle adressen zijn fictief.</p><ul style="display:grid;gap:24px">' + ''.join(rows) + '</ul></main></html>'
            return self.send(200, page, "text/html; charset=utf-8")
        if len(parts) == 2 and parts[0] == "outbox" and len(parts[1]) == 24 and all(c in "0123456789abcdef" for c in parts[1]):
            file = DOSSIER / "outbox" / (parts[1] + ".eml")
            if not file.is_file():
                return self.send(404, {"error": "not found"})
            message = BytesParser(policy=policy.default).parsebytes(file.read_bytes())
            return self.send(200, message.get_body(preferencelist=("html",)).get_content(), "text/html; charset=utf-8")
        if url.path == "/kantoor":
            return self.page("kantoor.html")
        if url.path == "/kantoor/export.csv":
            rows = dbx.export_rows()
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["klant"], delimiter=";")
            w.writeheader()
            w.writerows(rows)
            return self.send(200, "﻿" + buf.getvalue(), "text/csv; charset=utf-8",
                             {"Content-Disposition": f"attachment; filename=dossier-{QUARTER}.csv"})
        if parts[0] == "static" and len(parts) == 2 and (WEB / parts[1]).is_file():
            ctype = {".css": "text/css; charset=utf-8", ".woff2": "font/woff2"}.get(Path(parts[1]).suffix, "text/plain; charset=utf-8")
            return self.send(200, (WEB / parts[1]).read_bytes(), ctype)
        if parts[0] == "u" and len(parts) == 2:
            return self.page("portaal.html") if dbx.request_by_token(parts[1]) else self.send(404, "Onbekende link", "text/plain")
        if url.path == "/api/dashboard":
            d = dbx.dashboard()
            d.update(model=state["model"], queue=jobs.qsize() + (1 if state["busy"] else 0))
            return self.send(200, d)
        if parts[:2] == ["api", "u"] and len(parts) == 3:
            view = client_view(parts[2])
            return self.send(200, view) if view else self.send(404, {"error": "onbekend"})
        if parts[0] == "files" and len(parts) == 2 and parts[1].isdigit():
            u = dbx.upload(int(parts[1]))
            p = Path(u["path"])
            ctype = {".pdf": "application/pdf", ".json": "application/json"}.get(p.suffix.lower(), "image/jpeg")
            return self.send(200, p.read_bytes(), ctype)
        self.send(404, {"error": "niet gevonden"})

    def do_POST(self):
        host = self.headers.get("Host", "")
        if host not in {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}:
            return self.send(403, {"error": "local demo only"})
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{host}":
            return self.send(403, {"error": "origin rejected"})
        try:
            return self.handle_post()
        except (ValueError, KeyError, IndexError, json.JSONDecodeError):
            return self.send(400, {"error": "invalid demo request"})

    def handle_post(self):
        url = urllib.parse.urlparse(self.path)
        parts = url.path.strip("/").split("/")
        base = state["base_url"]
        if parts[:2] == ["api", "u"] and len(parts) == 4 and parts[3] == "upload":
            r = dbx.request_by_token(parts[2])
            qs = urllib.parse.parse_qs(url.query)
            slot, name = qs.get("slot", [""])[0], qs.get("name", ["document"])[0]
            size = int(self.headers.get("Content-Length") or 0)
            if not r or slot not in REQUIRED_ITEMS:
                return self.send(400, {"error": "ongeldige upload"})
            if not 0 < size <= MAX_UPLOAD:
                return self.send(413, {"error": "Bestand is te groot (max 20 MB)"})
            uid = dbx.add_upload(r, slot, name, self.rfile.read(size))
            jobs.put(uid)
            return self.send(200, {"upload_id": uid})
        if url.path == "/api/start":
            return self.send(200, {"started": dbx.start_quarter(base)})
        if url.path == "/api/clock":
            return self.send(200, {"actions": dbx.advance_clock(int(self.body_json().get("days", 3)), base)})
        if url.path == "/api/review":
            b = self.body_json()
            if b.get("decision") not in ("goedgekeurd", "afgewezen"):
                return self.send(400, {"error": "ongeldig besluit"})
            dbx.review(int(b["upload_id"]), b["decision"], base, b.get("note", ""))
            return self.send(200, {"ok": True})
        if url.path == "/api/demo/uploads":
            return self.send(200, {"queued": demo_uploads(int(self.body_json().get("round", 1)))})
        if url.path == "/api/reset":
            if state["busy"] or not jobs.empty():
                return self.send(409, {"error": "Wacht tot de controles klaar zijn."})
            while not jobs.empty():
                jobs.get_nowait()
                jobs.task_done()
            dbx.reset()
            # Old outbox files are retained; reset touches only generated demo database state.
            return self.send(200, {"ok": True})
        self.send(404, {"error": "niet gevonden"})


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--model", default=check_document.DEFAULT_MODEL)
    a = p.parse_args()
    state["model"] = a.model
    state["base_url"] = f"http://localhost:{a.port}"
    dbx.db()
    threading.Thread(target=worker, daemon=True).start()
    if a.model != "demo":
        threading.Thread(target=warm_up, daemon=True).start()
    print(f"Dossier Compleet draait op {state['base_url']}/kantoor  (model: {a.model}, mail: /outbox)")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
