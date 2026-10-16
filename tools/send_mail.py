"""HTML mail templates saved locally as .eml. This showcase has no mail-sending transport."""

import html
import secrets
from dossier_common import DOSSIER
from email.message import EmailMessage
from email.utils import formataddr

from dossier_common import OFFICE_NAME, first_name

FROM = formataddr((OFFICE_NAME, "documents@example.invalid"))
OFFICE = "office@example.invalid"
DEADLINE = "31 oktober"


def send(to, subject, paragraphs, button=None, items=None):
    """paragraphs: list of plain strings. button: (label, url). items: list of strings shown as a list."""
    text = "\n\n".join(paragraphs)
    if items:
        text += "\n\n" + "\n".join(f"- {i}" for i in items)
    if button:
        text += f"\n\n{button[0]}: {button[1]}"
    text += f"\n\nMet vriendelijke groet,\n{OFFICE_NAME}"

    font = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"  # mail clients don't load web fonts reliably
    body = "".join(f"<p style='margin:0 0 14px'>{html.escape(p)}</p>" for p in paragraphs)
    if items:  # the documents as an empty checklist, the same tick boxes as the portal
        body += (
            "<table role='presentation' cellpadding='0' cellspacing='0' style='margin:4px 0 18px;border-top:1px solid #E3E5E8;width:100%'>"
            + "".join(
                f"<tr><td style='padding:11px 12px 11px 0;border-bottom:1px solid #E3E5E8;width:18px;vertical-align:top'>"
                f"<div style='width:14px;height:14px;border:1.5px solid #9AA0A9;border-radius:4px;margin-top:2px'></div></td>"
                f"<td style='padding:11px 0;border-bottom:1px solid #E3E5E8'>{html.escape(i)}</td></tr>"
                for i in items
            )
            + "</table>"
        )
    if button:
        body += (
            f"<p style='margin:24px 0 8px'><a href='{button[1]}' style='background:#15181D;color:#fff;padding:12px 20px;"
            f"border-radius:8px;text-decoration:none;font-weight:600;display:inline-block'>{html.escape(button[0])}</a></p>"
            f"<p style='margin:0;color:#5C626C;font-size:13px'>Deze link is persoonlijk. Stuur hem niet door.</p>"
        )
    page = (
        f"<div style='background:#F1F2F4;padding:28px 12px;font-family:{font}'>"
        f"<div style='max-width:560px;margin:0 auto;background:#fff;border-radius:12px;padding:30px 32px;color:#15181D;"
        f"font-size:15.5px;line-height:1.55'><div style='font-weight:700;font-size:15px;padding-bottom:16px;margin-bottom:22px;"
        f"border-bottom:1px solid #E3E5E8'>{html.escape(OFFICE_NAME)}</div>{body}<p style='margin:26px 0 0;color:#5C626C'>"
        f"Met vriendelijke groet,<br>{html.escape(OFFICE_NAME)}</p></div>"
        f"<p style='max-width:560px;margin:14px auto 0;color:#5C626C;font-size:12px;text-align:center'>"
        f"Btw-aangifte 3e kwartaal 2026, uiterlijk {DEADLINE}</p></div>"
    )

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = FROM, to, subject
    msg.set_content(text)
    msg.add_alternative(page, subtype="html")
    outbox = DOSSIER / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / (secrets.token_hex(12) + ".eml")).write_bytes(msg.as_bytes())
    return subject


def request_mail(client, link, labels):
    first = first_name(client)
    return send(
        client["email"],
        "Je documenten voor de btw-aangifte Q3",
        [
            f"Hoi {first},",
            f"Het is weer tijd voor de btw-aangifte over het derde kwartaal. Deadline is {DEADLINE}. "
            "Wil je deze stukken uploaden via je persoonlijke link?",
        ],
        button=("Documenten uploaden", link),
        items=labels,
    )


def rejected_mail(client, link, label, reason_msg):
    first = first_name(client)
    return send(
        client["email"],
        f"Kun je dit document opnieuw sturen? ({label.split(' (')[0]})",
        [
            f"Hoi {first},",
            f"Dank je voor het uploaden. We hebben je {label.split(' (')[0].replace(' Q3 2026', '').lower()} bekeken, maar het klopt nog niet helemaal.",
            reason_msg,
        ],
        button=("Nieuw document uploaden", link),
    )


REMINDERS = {
    1: (
        "Kleine herinnering: je documenten voor Q3",
        "Een kleine herinnering: we hebben nog niet alles binnen voor je btw-aangifte over Q3.",
    ),
    2: (
        "We missen nog documenten voor je btw-aangifte",
        f"We hebben nog niet alles binnen voor je btw-aangifte. De deadline is {DEADLINE}; "
        "als we de stukken deze week hebben, lukt het ruim op tijd.",
    ),
    3: (
        f"Laatste herinnering: btw-aangifte Q3 (deadline {DEADLINE})",
        f"De deadline van {DEADLINE} komt dichtbij en we missen nog stukken. Lukt het niet? "
        "We bellen je deze week even, dan zoeken we samen een oplossing.",
    ),
}


def reminder_mail(client, link, n, missing):
    first = first_name(client)
    subject, line = REMINDERS[n]
    return send(
        client["email"],
        subject,
        [f"Hoi {first},", line, "Dit missen we nog:"],
        button=("Documenten uploaden", link),
        items=missing,
    )


def complete_mail(client):
    first = first_name(client)
    return send(
        client["email"],
        "Alles binnen voor je btw-aangifte Q3",
        [
            f"Hoi {first},",
            "Top, we hebben alles wat we nodig hebben voor je btw-aangifte over Q3. Je hoeft niets meer te doen; "
            "we laten het je weten als de aangifte is ingediend.",
        ],
    )


def office_review_mail(client, filename, office_msg, link):
    return send(
        OFFICE,
        f"Graag beoordelen: document van {client['business']}",
        [
            f"De documentcontrole twijfelt over '{filename}' van {client['business']} ({client['name']}):",
            office_msg,
        ],
        button=("Bekijk in het dashboard", link),
    )
