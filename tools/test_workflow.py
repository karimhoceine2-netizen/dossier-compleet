"""Integration checks for the demo workflow. Only temporary mock data is used."""
import json
import tempfile
import unittest
from pathlib import Path
import check_document
import dossier_db as db
import send_mail
from dossier_common import CLIENTS

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="dossier-test-")
        folder = Path(self.scratch.name)
        self.old = db.DB, db.UPLOADS, send_mail.DOSSIER
        db.DB, db.UPLOADS, send_mail.DOSSIER = folder / "demo.db", folder / "uploads", folder
        db.db()
        db.start_quarter("http://127.0.0.1:8787")
    def tearDown(self):
        db._conn.close()
        db._conn = None
        db.DB, db.UPLOADS, send_mail.DOSSIER = self.old
        self.scratch.cleanup()
    def upload(self, cid, slot, **changes):
        client = next(c for c in CLIENTS if c["id"] == cid)
        fields = dict(documentsoort="bankafschrift" if slot == "bank" else "factuur", leesbaar=True,
                      rekeninghouder=client["business"], afzender=client["business"] if slot == "verkoop" else "Demo Supplier",
                      geadresseerde=client["business"], datum_van="2026-07-01", datum_tot="2026-09-30",
                      totaal=242.0, btw=42.0, omschrijving="Mock fixture")
        fields.update(changes)
        request = db.q("SELECT * FROM requests WHERE client_id=?", cid)[0]
        uid = db.add_upload(request, slot, "mock.json", json.dumps({"demo_fixture": True, "fields": fields}).encode())
        result = check_document.check(db.upload(uid)["path"], slot, client, model="demo")
        db.apply_check(uid, result, "http://127.0.0.1:8787")
        return uid
    def test_request_is_idempotent_and_mail_stays_local(self):
        self.assertEqual(db.start_quarter("http://127.0.0.1:8787"), 0)
        self.assertEqual(len(db.q("SELECT * FROM requests")), 6)
        self.assertEqual(len(list((send_mail.DOSSIER / "outbox").glob("*.eml"))), 6)
    def test_completion_and_export(self):
        for slot in ("bank", "verkoop", "inkoop"):
            self.upload("demo-01", slot)
        self.assertEqual(db.dashboard()["kpi"]["compleet"], 1)
        self.assertEqual(len(db.export_rows()), 3)
        request = db.q("SELECT * FROM requests WHERE client_id='demo-01'")[0]
        self.assertFalse(db.maybe_complete(request["id"]))
    def test_human_review_and_reminder_escalation(self):
        uid = self.upload("demo-06", "bank", rekeninghouder="Unknown Demo")
        self.assertEqual(db.upload(uid)["status"], "twijfel")
        db.review(uid, "goedgekeurd", "http://127.0.0.1:8787")
        self.assertEqual(db.upload(uid)["decided_by"], "kantoor")
        db.advance_clock(3, "http://127.0.0.1:8787")
        count = len(db.q("SELECT * FROM mails"))
        self.assertEqual(db.run_reminders("http://127.0.0.1:8787"), [])
        self.assertEqual(len(db.q("SELECT * FROM mails")), count)
        db.advance_clock(11, "http://127.0.0.1:8787")
        self.assertEqual(db.q("SELECT call_needed FROM requests WHERE client_id='demo-05'")[0]["call_needed"], 1)
    def test_export_neutralizes_spreadsheet_formulas(self):
        client = next(c for c in CLIENTS if c["id"] == "demo-01")
        request = db.q("SELECT * FROM requests WHERE client_id='demo-01'")[0]
        fields = dict(documentsoort="bankafschrift", leesbaar=True, rekeninghouder=client["business"], afzender="Demo Bank",
                      geadresseerde=client["business"], datum_van="2026-07-01", datum_tot="2026-09-30", totaal=None, btw=None,
                      omschrijving="Mock fixture")
        uid = db.add_upload(request, "bank", '=HYPERLINK("http://example.invalid")', json.dumps({"demo_fixture": True, "fields": fields}).encode())
        db.apply_check(uid, check_document.check(db.upload(uid)["path"], "bank", client, model="demo"), "http://127.0.0.1:8787")
        row = db.export_rows()[0]
        self.assertTrue(row["bestand"].startswith("'="))
        self.assertEqual(db.csv_safe("12,50"), "12,50")
    def test_unmarked_json_is_rejected(self):
        p = Path(self.scratch.name) / "unmarked.json"
        p.write_text('{"fields": {}}')
        with self.assertRaises(ValueError):
            check_document.check(p, "bank", CLIENTS[0], model="demo")

if __name__ == "__main__":
    unittest.main()
