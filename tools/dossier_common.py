"""Shared paths and settings for the Dossier Compleet tools. Standard library only."""

from datetime import date

from common import TMP

DOSSIER = TMP / "dossier"
DEMO_DOCS = DOSSIER / "demo_docs"      # documents the demo clients "will upload"
MANIFEST = DEMO_DOCS / "manifest.json"  # which fixture each mock client uploads, in which round

OFFICE_NAME = "Voorbeeld Administratie"

# The quarter the demo asks documents for. The btw-aangifte for Q3 is due 31 October.
QUARTER = "2026-Q3"
QUARTER_START = date(2026, 7, 1)
QUARTER_END = date(2026, 9, 30)

# What every client has to deliver per quarter: slot id -> (label in the portal, accepted document type)
REQUIRED_ITEMS = {
    "bank": ("Bankafschrift Q3 2026 (1 juli t/m 30 september)", "bankafschrift"),
    "verkoop": ("Verkoopfacturen Q3 2026", "verkoopfactuur"),
    "inkoop": ("Inkoopfacturen en bonnen Q3 2026", "inkoopfactuur"),
}

# The fictional demo clients. Each one has a "story" that shows one part of the system.
# All identifiers are explicit mock values; emails are saved to a local outbox.
CLIENTS = [{'id': 'demo-01', 'name': 'Demo Alpha', 'business': 'Demo Studio Alpha', 'prefix': 'D1', 'email': 'demo-01@example.invalid', 'address': 'DEMO ADDRESS 01', 'iban': 'DEMO-ACCOUNT-01', 'story': 'Levert alles in één keer goed aan'}, {'id': 'demo-02', 'name': 'Demo Bravo', 'business': 'Demo Studio Bravo', 'prefix': 'D2', 'email': 'demo-02@example.invalid', 'address': 'DEMO ADDRESS 02', 'iban': 'DEMO-ACCOUNT-02', 'story': 'Stuurt eerst het bankafschrift van Q2'}, {'id': 'demo-03', 'name': 'Demo Charlie', 'business': 'Demo Studio Charlie', 'prefix': 'D3', 'email': 'demo-03@example.invalid', 'address': 'DEMO ADDRESS 03', 'iban': 'DEMO-ACCOUNT-03', 'story': 'Maakt een wazige foto van een bon'}, {'id': 'demo-04', 'name': 'Demo Delta', 'business': 'Demo Studio Delta', 'prefix': 'D4', 'email': 'demo-04@example.invalid', 'address': 'DEMO ADDRESS 04', 'iban': 'DEMO-ACCOUNT-04', 'story': 'Uploadt de energierekening als bankafschrift'}, {'id': 'demo-05', 'name': 'Demo Echo', 'business': 'Demo Studio Echo', 'prefix': 'D5', 'email': 'demo-05@example.invalid', 'address': 'DEMO ADDRESS 05', 'iban': 'DEMO-ACCOUNT-05', 'story': 'Reageert nergens op'}, {'id': 'demo-06', 'name': 'Demo Foxtrot', 'business': 'Demo Studio Foxtrot', 'prefix': 'D6', 'email': 'demo-06@example.invalid', 'address': 'DEMO ADDRESS 06', 'iban': 'DEMO-ACCOUNT-06', 'story': 'Uploadt een bankafschrift op naam van iemand anders'}]

