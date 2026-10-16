"""Check one uploaded document with a local vision model (Ollama), then let fixed rules decide.

The model only READS the document (type, names, period, amounts). Python decides whether it is
accepted, rejected or needs a human, so the outcome is predictable and explainable.

    python3 tools/check_document.py <file> --slot bank --client demo-02 [--model qwen2.5vl:7b]
"""

import argparse
import base64
import hashlib
import json
import os
import re
import struct
import subprocess
import tempfile
import time
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from common import http
from dossier_common import CLIENTS, DOSSIER, QUARTER_END, QUARTER_START, REQUIRED_ITEMS

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("DOSSIER_MODEL", "demo")  # no external service required for the default demo
PROMPT_VERSION = 3  # bump when PROMPT, SCHEMA, IMAGE_SIZE or page handling changes, so cached readings are not reused
IMAGE_SIZE = int(os.environ.get("DOSSIER_IMAGE_SIZE", "1024"))  # longest side in px; fewer pixels = faster
MAX_PAGES = 4  # pages sent to the model; longer PDFs send their first two and last two pages
CACHE = DOSSIER / "cache"
PDF_PAGES_SRC = Path(__file__).with_name("pdf_pages.swift")
PDF_PAGES_BIN = DOSSIER.parent / "bin" / "pdf_pages"
BLUR_MIN = 40  # photo sharpness below this = unreadable (demo photos: blurry 6, sharp ~180)
PLACEHOLDER_AMOUNTS = {1234.56, 123.45, 12345.67}  # numbers models invent when they cannot read a document

PROMPT = """You check a document that a client uploaded to their Dutch accounting office.
Read the image carefully and fill in every JSON field. Copy names and dates exactly as printed.

- documentsoort: "bankafschrift" (bank statement listing transactions of one account),
  "factuur" (invoice from a company or freelancer), "kassabon" (shop or till receipt),
  or "overig" (anything else: energy bill, letter, contract, ID card, screenshot, ...).
- leesbaar: true if you can read the text. A watermark such as "VOORBEELD" is normal: ignore it.
  false only if the photo is blurred, out of focus, too dark or cut off so the text cannot be read.
- rekeninghouder: bank statements only, the account holder ("Rekeninghouder"). Otherwise "".
- afzender: who issued the document (company name at the top; for a bank statement the bank).
- geadresseerde: who the document is addressed to ("Aan", "Factuur aan", the customer). "" if none.
- datum_van, datum_tot: the period the document covers as YYYY-MM-DD. Bank statement: the statement period.
  Invoice or receipt: both are the document date ("Datum" or "Factuurdatum", near the invoice number,
  or the date printed on the receipt). Dutch dates are written DD-MM-YYYY. "" if not visible.
- totaal: total amount including VAT as a number (1234.56), null if there is none.
- btw: VAT amount as a number, null if there is none.
- omschrijving: one short Dutch sentence saying what the document is."""

SCHEMA = {
    "type": "object",
    "properties": {
        "documentsoort": {"type": "string", "enum": ["bankafschrift", "factuur", "kassabon", "overig"]},
        "leesbaar": {"type": "boolean"},
        "rekeninghouder": {"type": "string"},
        "afzender": {"type": "string"},
        "geadresseerde": {"type": "string"},
        "datum_van": {"type": "string"},
        "datum_tot": {"type": "string"},
        "totaal": {"type": ["number", "null"]},
        "btw": {"type": ["number", "null"]},
        "omschrijving": {"type": "string"},
    },
    "required": ["documentsoort", "leesbaar", "rekeninghouder", "afzender", "geadresseerde",
                 "datum_van", "datum_tot", "totaal", "btw", "omschrijving"],
}

SLOT_OF_KIND = {"bankafschrift": "bank", "verkoopfactuur": "verkoop", "inkoopfactuur": "inkoop"}
KIND_LABEL = {"bankafschrift": "een bankafschrift", "verkoopfactuur": "een verkoopfactuur",
              "inkoopfactuur": "een inkoopfactuur of bon", "overig": "geen bankafschrift, factuur of bon"}
MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
          "oktober", "november", "december"]


# ---------- reading (AI) ----------

def pdf_pages_bin():
    """The PDF page renderer, compiled once from pdf_pages.swift (and again when the source changes)."""
    if not PDF_PAGES_BIN.exists() or PDF_PAGES_BIN.stat().st_mtime < PDF_PAGES_SRC.stat().st_mtime:
        PDF_PAGES_BIN.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(PDF_PAGES_SRC), "-o", str(PDF_PAGES_BIN)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return PDF_PAGES_BIN


def pages_to_send(count):
    """All pages of a short PDF; of a long one the first two and last two (names and periods sit at the
    start, totals and closing balances at the end)."""
    if count <= MAX_PAGES:
        return list(range(1, count + 1))
    return [1, 2, count - 1, count]


def to_images(path, workdir):
    """PDF -> one PNG per page sent (see pages_to_send); a photo -> one JPG scaled down to IMAGE_SIZE.
    Returns (images, page_count)."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        def run(pages):
            return subprocess.run([str(pdf_pages_bin()), str(path), workdir, str(IMAGE_SIZE), pages],
                                  check=True, capture_output=True, text=True).stdout.split("\n")
        count = int(run("")[0])
        images = [Path(p) for p in run(",".join(map(str, pages_to_send(count))))[1:] if p]
    else:
        count, out = 1, Path(workdir) / (path.stem + ".jpg")
        subprocess.run(["sips", "-Z", str(IMAGE_SIZE), "-s", "format", "jpeg", str(path), "--out", str(out)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        images = [out]
    if not images or not all(i.exists() for i in images):
        raise RuntimeError(f"Could not turn {path.name} into an image")
    return images, count


def read_document(path, model=DEFAULT_MODEL, use_cache=True):
    """Ask the vision model what is on the document. Returns (fields, seconds, cached)."""
    raw = Path(path).read_bytes()
    key = hashlib.sha256(raw + f"{model}|{PROMPT_VERSION}|{IMAGE_SIZE}".encode()).hexdigest()[:24]
    cache_file = CACHE / model.replace(":", "_").replace("/", "_") / f"{key}.json"
    if use_cache and cache_file.exists():
        hit = json.loads(cache_file.read_text())
        return hit["fields"], hit["seconds"], True

    with tempfile.TemporaryDirectory() as tmp:
        images, count = to_images(path, tmp)
        b64 = [base64.b64encode(i.read_bytes()).decode() for i in images]
    prompt = PROMPT
    if len(images) > 1:
        shown = pages_to_send(count)
        prompt += (f"\n\nThe document has {count} pages. The {len(images)} images are pages "
                   f"{', '.join(map(str, shown))}, in that order. Read them together as one document: "
                   "names and periods are usually on the first page, totals on the last.")
    body = {"model": model, "stream": False, "format": SCHEMA, "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "user", "content": prompt, "images": b64}]}
    start = time.monotonic()
    status, _, text, _ = http(f"{OLLAMA}/api/chat", method="POST", data=body, timeout=600)
    seconds = round(time.monotonic() - start, 1)
    if status != 200:
        raise RuntimeError(f"Ollama error {status}: {text[:300]}")
    fields = json.loads(json.loads(text)["message"]["content"])
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"fields": fields, "seconds": seconds}, ensure_ascii=False, indent=1))
    return fields, seconds, False


def sharpness(path, size=800):
    """Measured sharpness of a photo: the 99.5th percentile of the Laplacian (edge strength) on a grey
    version. Text in focus gives strong edges; a blurred photo gives almost none. Independent of the AI,
    which happily "reads" (invents) text on blurred photos."""
    with tempfile.TemporaryDirectory() as tmp:
        bmp = Path(tmp) / "x.bmp"
        subprocess.run(["sips", "-Z", str(size), "-s", "format", "bmp", str(path), "--out", str(bmp)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        b = bmp.read_bytes()
    offset, (w, h), bpp = struct.unpack_from("<I", b, 10)[0], struct.unpack_from("<ii", b, 18), struct.unpack_from("<H", b, 28)[0]
    step, row_len = bpp // 8, ((bpp * w + 31) // 32) * 4
    gray = []
    for y in range(abs(h)):
        row = b[offset + y * row_len: offset + y * row_len + w * step]
        gray.append([(row[i + 2] * 299 + row[i + 1] * 587 + row[i] * 114) // 1000 for i in range(0, w * step, step)])
    edges = []
    for y in range(1, len(gray) - 1):
        up, cur, down = gray[y - 1], gray[y], gray[y + 1]
        edges.extend(abs(4 * cur[x] - cur[x - 1] - cur[x + 1] - up[x] - down[x]) for x in range(1, w - 1))
    edges.sort()
    return edges[int(len(edges) * 0.995)] if edges else 0


# ---------- deciding (rules) ----------

GENERIC = {"demo", "studio", "bv", "nv", "vof", "de", "het", "van", "der", "den", "ter", "tav", "fam", "en"}


def tokens(name):
    words = re.sub(r"[^a-z0-9à-ÿ]+", " ", (name or "").lower()).split()
    return {w for w in words if len(w) > 1 and w not in GENERIC}


def name_matches(found, client):
    """True if a printed name plausibly belongs to the client (business name or own name)."""
    found_t = tokens(found)
    if not found_t:
        return False
    # Banks often print "D. Foxtrot": accept surname + matching (or no) initial, but not "X. Foxtrot".
    first, *rest = client["name"].split()
    surname_t = tokens(" ".join(rest))
    initials = set(re.findall(r"\b([a-z])\b\.?", (found or "").lower()))
    if surname_t and surname_t <= found_t:
        return not initials or first[0].lower() in initials
    for candidate in (client["business"], client["name"]):
        cand_t = tokens(candidate)
        if cand_t and len(cand_t & found_t) / len(cand_t) >= 0.6:
            return True
        if SequenceMatcher(None, " ".join(sorted(cand_t)), " ".join(sorted(found_t))).ratio() >= 0.85:
            return True
    return False


def parse_date(s):
    try:
        return date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        m = re.match(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", (s or "").strip())
        return date(int(m[3]), int(m[2]), int(m[1])) if m else None


def nl(d):
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def decide(fields, slot, client, q_start=QUARTER_START, q_end=QUARTER_END):
    """Turn what the model read into a verdict. Returns a dict with verdict, reason, slot and messages."""
    soort = fields.get("documentsoort", "overig")
    wanted_label = REQUIRED_ITEMS[slot][0]
    out = {"verdict": "goedgekeurd", "reason": "ok", "slot": slot, "kind": soort, "client_msg": "", "office_msg": ""}

    def result(verdict, reason, client_msg, office_msg="", **extra):
        out.update(verdict=verdict, reason=reason, client_msg=client_msg, office_msg=office_msg or client_msg, **extra)
        return out

    if fields.get("scherpte") is not None and fields["scherpte"] < BLUR_MIN:
        return result("afgewezen", "onleesbaar",
                      "De foto is te wazig om te lezen. Wil je een nieuwe foto maken bij goed licht, "
                      "recht boven het document en met de camera scherpgesteld, of de PDF uploaden?")

    # Models often say "not readable" while reading everything fine (e.g. because of the watermark),
    # so only reject when the key facts are also missing.
    has_date = bool(parse_date(fields.get("datum_van")) or parse_date(fields.get("datum_tot")))
    has_key_facts = has_date and (fields.get("totaal") is not None or soort == "bankafschrift")
    if not fields.get("leesbaar", True) and not has_key_facts:
        return result("afgewezen", "onleesbaar",
                      "Dit document is niet goed leesbaar (wazig, donker of afgesneden). Wil je een nieuwe foto "
                      "maken bij goed licht, recht boven het document, of de PDF uploaden?")

    if {fields.get("totaal"), fields.get("btw")} & PLACEHOLDER_AMOUNTS:
        return result("twijfel", "verdachte_waarden",
                      "Dank je! Een medewerker van het kantoor bekijkt dit document nog even.",
                      f"De AI las bedragen die op opvulwaarden lijken ({fields.get('totaal')} / {fields.get('btw')}); "
                      "mogelijk kon het document niet goed gelezen worden. Graag handmatig beoordelen.")

    # What kind of document is it, from the client's point of view?
    if soort == "bankafschrift":
        kind, who = "bankafschrift", fields.get("rekeninghouder") or fields.get("geadresseerde")
    elif soort == "kassabon":
        kind, who = "inkoopfactuur", None  # receipts carry no client name
    elif soort == "factuur":
        if name_matches(fields.get("afzender"), client):
            kind, who = "verkoopfactuur", fields.get("afzender")
        elif name_matches(fields.get("geadresseerde"), client):
            kind, who = "inkoopfactuur", fields.get("geadresseerde")
        else:
            return result("twijfel", "andere_naam",
                          "Dank je! Een medewerker van het kantoor bekijkt dit document nog even.",
                          f"Factuur van '{fields.get('afzender')}' aan '{fields.get('geadresseerde')}': geen van beide "
                          f"lijkt op {client['business']} of {client['name']}. Graag handmatig beoordelen.")
    else:
        kind, who = "overig", None
    out["kind"] = kind

    desc = (fields.get("omschrijving") or "een ander soort document").rstrip(".").lower()
    if kind == "overig" or SLOT_OF_KIND.get(kind) not in REQUIRED_ITEMS:
        return result("afgewezen", "verkeerd_type",
                      f"Je uploadde dit bij '{wanted_label}', maar het lijkt {KIND_LABEL['overig']} "
                      f"({desc}). Wil je het juiste document uploaden?")
    if slot == "bank" and kind != "bankafschrift":
        # The client meant this as their bank statement; filing it elsewhere would hide that the statement is missing.
        return result("afgewezen", "verkeerd_type",
                      f"Je uploadde dit bij '{wanted_label}', maar het is geen bankafschrift ({desc}). "
                      "Wil je het afschrift als PDF downloaden in je bank-app en dat uploaden?")

    right_slot = SLOT_OF_KIND[kind]
    if right_slot != slot:
        # A sales/purchase invoice (or a statement) in the wrong box: file it where it belongs instead of bothering the client.
        out["slot"] = right_slot
        out["moved"] = True

    if kind == "bankafschrift" and not name_matches(who, client):
        return result("twijfel", "andere_naam",
                      "Dank je! Een medewerker van het kantoor bekijkt dit afschrift nog even.",
                      f"Bankafschrift staat op naam van '{who or 'onbekend'}', niet van {client['business']} "
                      f"of {client['name']}. Privérekening of partner? Graag handmatig beoordelen.")

    start, end = parse_date(fields.get("datum_van")), parse_date(fields.get("datum_tot"))
    start, end = start or end, end or start
    if not start:
        return result("twijfel", "periode_onbekend",
                      "Dank je! Een medewerker van het kantoor bekijkt dit document nog even.",
                      "De AI kon geen datum of periode vinden. Graag handmatig beoordelen.")
    out["period"] = [start.isoformat(), end.isoformat()]

    if end < q_start or start > q_end:
        if kind == "bankafschrift":
            msg = (f"Dit bankafschrift gaat over {nl(start)} t/m {nl(end)}. Voor de btw-aangifte hebben we het "
                   f"afschrift over {nl(q_start)} t/m {nl(q_end)} nodig.")
        else:
            msg = (f"Deze {'factuur' if soort == 'factuur' else 'bon'} is van {nl(start)} en valt dus niet in "
                   f"dit kwartaal ({nl(q_start)} t/m {nl(q_end)}). Heb je de juiste geüpload?")
        return result("afgewezen", "verkeerde_periode", msg)

    if kind == "bankafschrift" and (start > q_start + timedelta(days=3) or end < q_end - timedelta(days=3)):
        return result("twijfel", "deel_periode",
                      "Dank je! Een medewerker van het kantoor bekijkt dit afschrift nog even.",
                      f"Afschrift dekt {nl(start)} t/m {nl(end)}, niet het hele kwartaal. Mogelijk ontbreken maanden.")

    moved = f" We hebben hem bij '{REQUIRED_ITEMS[right_slot][0]}' gezet." if out.get("moved") else ""
    return result("goedgekeurd", "ok", f"Ontvangen en goedgekeurd: {KIND_LABEL[kind]}.{moved}")


def known_name(found, client):
    """The client's own spelling of a name the model read, if that name belongs to the client.
    A model can drop a letter ("Demo Stdio Alpha"); people and the bookkeeping export should see the real name."""
    if not found or not name_matches(found, client):
        return None
    score = lambda name: SequenceMatcher(None, found.lower(), name.lower()).ratio()
    return client["business"] if score(client["business"]) >= score(client["name"]) else client["name"]


def check(path, slot, client, model=DEFAULT_MODEL, use_cache=True):
    if model == "demo":
        fixture = json.loads(Path(path).read_text())
        if fixture.get("demo_fixture") is not True or not isinstance(fixture.get("fields"), dict):
            raise ValueError("Demo mode accepts only the bundled mock JSON fixtures.")
        fields = fixture["fields"]
        result = decide(fields, slot, client)
        result.update(fields=fields, seconds=0, cached=False, model="demo")
        return result
    fields, seconds, cached = read_document(path, model, use_cache)
    if Path(path).suffix.lower() != ".pdf":  # PDFs are digital and sharp; photos get measured
        fields = {**fields, "scherpte": sharpness(path)}
    herkend = {k: n for k in ("rekeninghouder", "afzender", "geadresseerde") if (n := known_name(fields.get(k), client))}
    if herkend:
        fields = {**fields, "herkend": herkend}
    decision = decide(fields, slot, client)
    decision.update(fields=fields, seconds=seconds, cached=cached, model=model)
    return decision


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("file")
    p.add_argument("--slot", required=True, choices=list(REQUIRED_ITEMS))
    p.add_argument("--client", required=True, choices=[c["id"] for c in CLIENTS])
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--no-cache", action="store_true")
    a = p.parse_args()
    client = next(c for c in CLIENTS if c["id"] == a.client)
    print(json.dumps(check(a.file, a.slot, client, a.model, not a.no_cache), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
