"""Regression test for the decision rules in check_document.decide(). No model needed; runs in a second.

    python3 tools/test_dossier_rules.py
"""

import sys

from check_document import decide
from dossier_common import CLIENTS

C = {c["id"]: c for c in CLIENTS}
BASE = dict(leesbaar=True, rekeninghouder="", afzender="", geadresseerde="", datum_van="", datum_tot="",
            totaal=None, btw=None, omschrijving="")
Q3 = dict(datum_van="2026-07-01", datum_tot="2026-09-30")


def f(**k):
    return {**BASE, **k}


CASES = [
    ("different mock client", f(documentsoort="bankafschrift", rekeninghouder="Demo Studio Bravo", **Q3), "bank", "demo-01", "twijfel/andere_naam"),
    ("Q3 statement", f(documentsoort="bankafschrift", rekeninghouder="Demo Studio Bravo", **Q3), "bank", "demo-02", "goedgekeurd/ok"),
    ("Q2 statement", f(documentsoort="bankafschrift", rekeninghouder="Demo Studio Bravo", datum_van="2026-04-01",
                       datum_tot="2026-06-30"), "bank", "demo-02", "afgewezen/verkeerde_periode"),
    ("statement, other person", f(documentsoort="bankafschrift", rekeninghouder="Unknown Demo", **Q3), "bank", "demo-06", "twijfel/andere_naam"),
    ("statement, initial + surname", f(documentsoort="bankafschrift", rekeninghouder="D. Foxtrot", datum_van="01-07-2026",
                                       datum_tot="30-09-2026"), "bank", "demo-06", "goedgekeurd/ok"),
    ("statement, partner same surname", f(documentsoort="bankafschrift", rekeninghouder="X. Foxtrot", **Q3), "bank", "demo-06", "twijfel/andere_naam"),
    ("statement, only July", f(documentsoort="bankafschrift", rekeninghouder="Demo Studio Charlie", datum_van="2026-07-01",
                               datum_tot="2026-07-31"), "bank", "demo-03", "twijfel/deel_periode"),
    ("energy bill as 'overig'", f(documentsoort="overig", afzender="Voorbeeld Energie", geadresseerde="Demo Delta",
                                  datum_van="2025-09-01", datum_tot="2026-08-31", totaal=-88.83), "bank", "demo-04", "afgewezen/verkeerd_type"),
    ("energy bill as 'factuur' in bank slot", f(documentsoort="factuur", afzender="Voorbeeld Energie", geadresseerde="Demo Delta",
                                                datum_van="2026-09-12", totaal=-88.83), "bank", "demo-04", "afgewezen/verkeerd_type"),
    ("statement in purchase slot is moved", f(documentsoort="bankafschrift", rekeninghouder="Demo Studio Delta", **Q3),
     "inkoop", "demo-04", "goedgekeurd/ok"),
    ("blurry receipt", f(documentsoort="kassabon", leesbaar=False), "inkoop", "demo-03", "afgewezen/onleesbaar"),
    ("blurry photo, model invents a valid Q3 receipt", f(documentsoort="kassabon", afzender="Shop", datum_van="2026-08-06",
                                                         totaal=20.0, scherpte=6), "inkoop", "demo-03", "afgewezen/onleesbaar"),
    ("sharp photo", f(documentsoort="kassabon", afzender="Shop", datum_van="2026-08-06", totaal=20.0, scherpte=180),
     "inkoop", "demo-03", "goedgekeurd/ok"),
    ("placeholder amounts", f(documentsoort="kassabon", afzender="Shop", datum_van="2026-08-06", totaal=1234.56, btw=123.45),
     "inkoop", "demo-03", "twijfel/verdachte_waarden"),
    ("model says unreadable but read everything", f(documentsoort="factuur", leesbaar=False, afzender="Demo Studio Alpha",
                                                    geadresseerde="Bakkerij Voorbeeld", datum_van="14-07-2026", totaal=1028.5),
     "verkoop", "demo-01", "goedgekeurd/ok"),
    ("purchase invoice in sales slot is moved", f(documentsoort="factuur", afzender="Voorbeeld Software B.V.",
                                                  geadresseerde="Demo Studio Alpha", datum_van="2026-08-01", totaal=108.86),
     "verkoop", "demo-01", "goedgekeurd/ok"),
    ("invoice from Q4", f(documentsoort="factuur", afzender="Demo Studio Alpha", geadresseerde="X", datum_van="2026-10-03",
                          totaal=10), "verkoop", "demo-01", "afgewezen/verkeerde_periode"),
    ("invoice between strangers", f(documentsoort="factuur", afzender="Iemand B.V.", geadresseerde="Ander B.V.", datum_van="2026-08-03",
                                    totaal=10), "inkoop", "demo-01", "twijfel/andere_naam"),
    ("receipt without date", f(documentsoort="kassabon", afzender="Shop", totaal=12.5), "inkoop", "demo-01", "twijfel/periode_onbekend"),
]


def main():
    failures = 0
    for name, fields, slot, cid, expected in CASES:
        r = decide(fields, slot, C[cid])
        got = f"{r['verdict']}/{r['reason']}"
        ok = got == expected
        failures += not ok
        print(f"{'OK' if ok else 'XX'}  {name:<44} {got}" + ("" if ok else f"   (expected {expected})"))
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
