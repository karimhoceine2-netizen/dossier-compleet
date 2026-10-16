"""Create explicit mock extraction fixtures. No PDFs, real identities, or model calls."""
import json
from dossier_common import CLIENTS, DEMO_DOCS, MANIFEST

def build():
    DEMO_DOCS.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, client in enumerate(CLIENTS):
        if i == 4:  # no uploads: demonstrates reminders
            continue
        for slot in ("bank", "verkoop", "inkoop"):
            fields = dict(documentsoort="bankafschrift" if slot == "bank" else "factuur",
                          leesbaar=True, rekeninghouder=client["business"] if slot == "bank" else "",
                          afzender={"verkoop": client["business"], "bank": "Demo Bank"}.get(slot, "Demo Supplier"),
                          geadresseerde=client["business"], datum_van="2026-07-01", datum_tot="2026-09-30",
                          totaal=None if slot == "bank" else 242.0, btw=None if slot == "bank" else 42.0,
                          omschrijving="Explicit mock extraction fixture")
            first = dict(fields)
            if i == 1 and slot == "bank":
                first.update(datum_van="2026-04-01", datum_tot="2026-06-30")
            if i == 2 and slot == "inkoop":
                first.update(leesbaar=False, scherpte=6)
            if i == 3 and slot == "bank":
                first.update(documentsoort="overig", omschrijving="Demo energy bill")
            if i == 5 and slot == "bank":
                first.update(rekeninghouder="Unknown Demo")
            for round_no, value in [(1, first)] + ([(2, fields)] if first != fields and i != 5 else []):
                filename = f"{client['id']}-{slot}-round-{round_no}.json"
                (DEMO_DOCS / filename).write_text(json.dumps({"demo_fixture": True, "fields": value}, indent=2))
                manifest.append({"client": client["id"], "slot": slot, "file": filename, "upload_round": round_no})
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    return len(manifest)
if __name__ == "__main__":
    print(f"Created {build()} mock extraction fixtures.")
