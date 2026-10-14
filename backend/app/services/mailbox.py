"""Hosted POP3/SMTP mailbox support with an internal approval draft box."""

import email
import mimetypes
import poplib
import re
import smtplib
import uuid
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage as RfcEmailMessage
from email.utils import make_msgid
from pathlib import Path

from app.core.config import settings
from app.models.enterprise import EmailBounceEvent, EmailMessage


def configuration_status() -> dict:
    return {
        "pop3_configured": bool(settings.pop3_host and settings.pop3_username and settings.pop3_password),
        "smtp_configured": bool(settings.smtp_host and settings.smtp_username and settings.smtp_password),
        "smtp_send_enabled": settings.mail_send_enabled,
    }


def _text(value: str | None) -> str:
    return str(make_header(decode_header(value or "")))


def _body(message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    return message.get_payload(decode=True).decode(message.get_content_charset() or "utf-8", errors="replace")


# ── Bounce (NDR) detection ────────────────────────────────────────────
#
# When SMTP send fails at the recipient's mail server (bad address, full
# mailbox, blocked domain, ...) that server mails a Non-Delivery-Report
# (NDR) back to our own mailbox — the same mailbox poll_pop3() already
# polls. We look for that pattern here so bounces surface automatically
# on every poll, with the "why" (SMTP status code + diagnostic text)
# visible via GET /mail/bounces.

_BOUNCE_SUBJECT_RE = re.compile(
    r"(undeliver|delivery status notification|delivery.?failure|"
    r"returned to sender|failure notice|mail delivery failed|"
    r"could not be delivered|delivery has failed)",
    re.IGNORECASE,
)
# The DSN 'Status:' field (RFC 3464) is the most reliable source of the
# extended status code — check it first, before any free-text scanning.
_STATUS_FIELD_RE = re.compile(r"^Status:\s*([245]\.\d{1,3}\.\d{1,3})", re.IGNORECASE | re.MULTILINE)
# Negative lookarounds exclude IPv4 octets — an extended code is X.Y.Z and
# never followed by a 4th ".digit" the way "mx.example.com[1.2.3.4]" is.
_EXTENDED_CODE_RE = re.compile(r"(?<!\d\.)\b([245])\.\d{1,3}\.\d{1,3}\b(?!\.\d)")
_BASIC_CODE_RE = re.compile(r"(?<!\.)\b([245])\d{2}\b(?!\.\d)")
_RECIPIENT_RE = re.compile(
    r"(?:Final-Recipient|Original-Recipient)\s*:\s*(?:rfc822;)?\s*([^\s,;]+@[^\s,;>]+)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_IP_BRACKET_RE = re.compile(r"\[[0-9.]+\]")
_DIAGNOSTIC_KEYWORDS = ("diagnostic-code", "status:", "smtp error", "said:", "smtp;")


def _classify_bounce(subject: str, body: str) -> dict | None:
    """Best-effort NDR parse. Returns None if this doesn't look like a bounce.

    NDR formats vary a lot by mail server, so this is deliberately
    forgiving: it keys off the subject line (the one thing almost every
    server gets right), prefers the structured DSN 'Status:' field for the
    code, and falls back to scanning the diagnostic-looking line only —
    never the whole body, which risks matching IP address octets instead
    of a real status code (a bracketed remote-server IP is scrubbed too).
    """
    if not _BOUNCE_SUBJECT_RE.search(subject or ""):
        return None

    body = body or ""
    scrubbed = _IP_BRACKET_RE.sub("", body)

    recipient_match = _RECIPIENT_RE.search(body)
    recipient = recipient_match.group(1) if recipient_match else None
    if not recipient:
        candidates = _EMAIL_RE.findall(body)
        recipient = candidates[0] if candidates else None
    if not recipient:
        return None

    diagnostic = ""
    for line in scrubbed.splitlines():
        low = line.lower()
        if any(keyword in low for keyword in _DIAGNOSTIC_KEYWORDS):
            diagnostic = line.strip()
            break

    status_match = _STATUS_FIELD_RE.search(body)
    smtp_code, bounce_class = None, "unknown"
    if status_match:
        smtp_code = status_match.group(1)
        bounce_class = {"5": "hard", "4": "soft"}.get(smtp_code[0], "unknown")
    else:
        scan_text = diagnostic or scrubbed
        code_match = _EXTENDED_CODE_RE.search(scan_text) or _BASIC_CODE_RE.search(scan_text)
        if code_match:
            smtp_code = code_match.group(0)
            bounce_class = {"5": "hard", "4": "soft"}.get(code_match.group(1), "unknown")

    if not diagnostic:
        diagnostic = (subject or "")[:300]

    return {
        "email": recipient.strip().strip("<>").lower(),
        "bounce_class": bounce_class,
        "smtp_code": smtp_code,
        "diagnostic": diagnostic[:2000],
    }


def process_bounce_notifications(db, rows: list[EmailMessage]) -> list[EmailBounceEvent]:
    """Scan newly-polled inbound messages for bounce NDRs and record them.

    Hard bounces (SMTP 5xx — bad address, domain doesn't exist, mailbox
    permanently disabled) are recorded and will be excluded from future
    K3 campaign email sends by is_email_suppressed(). Soft bounces (4xx —
    mailbox full, greylisting, temporary DNS trouble) are recorded for
    visibility only; they're usually transient and often succeed on retry.
    """
    events = []
    for row in rows:
        parsed = _classify_bounce(row.subject, row.body)
        if parsed is None:
            continue
        events.append(EmailBounceEvent(
            email=parsed["email"],
            bounce_class=parsed["bounce_class"],
            smtp_code=parsed["smtp_code"],
            diagnostic=parsed["diagnostic"],
            source_subject=row.subject,
            email_message_id=row.id,
        ))
    if events:
        db.add_all(events)
        db.commit()
    return events


def is_email_suppressed(db, email_addr: str | None) -> bool:
    """True if this address has a recorded hard bounce and should be
    skipped by all future campaign email sends."""
    if not email_addr:
        return False
    return db.query(EmailBounceEvent.id).filter(
        EmailBounceEvent.email == email_addr.strip().lower(),
        EmailBounceEvent.bounce_class == "hard",
    ).first() is not None


# ── Campaign reply detection ──────────────────────────────────────────
#
# A reply from a campaign recipient lands in the same POP3 mailbox. We
# match the sender against CampaignContact.email, stamp response_ts (which
# stops the drip — see K3 _drip) and classify it with K5's keywords.
# Only the new text is read: the quoted original contains our own footer
# ("unsubscribe") and would otherwise mark every reply as an opt-out.

_AUTO_REPLY_SUBJECT_RE = re.compile(
    r"^\s*(automatic reply|auto(?:matic)?[- ]?reply|autoreply|out of (?:the )?office|"
    r"ooo\b|auto:|away from (?:the )?office|on leave)",
    re.IGNORECASE,
)
_QUOTE_START_RE = re.compile(
    r"^\s*(on .{0,200}wrote:\s*$|-{2,}\s*original message\s*-{2,}|from:\s|sent from my |"
    r"_{5,})",
    re.IGNORECASE | re.MULTILINE,
)
_OPTOUT_REPLY_RE = re.compile(
    r"\b(unsubscribe|remove me|take me off|stop (?:emailing|sending|mailing)|"
    r"do not (?:email|contact)|don't (?:email|contact))\b",
    re.IGNORECASE,
)


def _new_reply_text(body: str | None) -> str:
    """The part of a reply above the quoted original, minus '>' lines."""
    body = body or ""
    m = _QUOTE_START_RE.search(body)
    if m:
        body = body[:m.start()]
    return "\n".join(l for l in body.splitlines() if not l.lstrip().startswith(">")).strip()


_OOO_RETURN_RE = re.compile(
    r"(?:until|till|back on|back by|returning on|return on|returning|back)\s+(?:[A-Za-z]+day,?\s+)?"
    r"(\d{1,2})(?:st|nd|rd|th)?[\s/.-]+([A-Za-z]{3,9}|\d{1,2})(?:[\s/.,-]+(\d{2,4}))?",
    re.IGNORECASE,
)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _ooo_return_date(text: str, now: datetime):
    """Return date stated in an out-of-office message, else now + 7 days.
    Capped at 30 days so a typo never parks a contact for a year."""
    from datetime import timedelta
    m = _OOO_RETURN_RE.search(text or "")
    if m:
        day, mon, yr = m.group(1), m.group(2), m.group(3)
        month = int(mon) if mon.isdigit() else _MONTHS.get(mon[:3].lower())
        try:
            year = int(yr) + (2000 if yr and len(yr) == 2 else 0) if yr else now.year
            d = datetime(year, month, int(day), tzinfo=timezone.utc)
            if not yr and d < now - timedelta(days=1):
                d = d.replace(year=now.year + 1)
            if now - timedelta(days=1) <= d <= now + timedelta(days=30):
                return d
        except (TypeError, ValueError):
            pass
    return now + timedelta(days=7)


def process_campaign_replies(db, rows: list[EmailMessage]) -> list[dict]:
    """Mark campaign contacts who replied; opt out explicit 'remove me' replies.
    Bounces and out-of-office auto-replies are ignored (they are not replies
    and must not stop a sequence)."""
    from email.utils import parseaddr
    from sqlalchemy import func
    from app.agents.k5_response_tracker import _classify_keyword
    from app.models.enterprise import CampaignContact, ContactOptOut

    now = datetime.now(timezone.utc)
    recorded: list[dict] = []
    for row in rows:
        if row.direction != "in":
            continue
        subject = row.subject or ""
        if _classify_bounce(subject, row.body or "") is not None:
            continue
        addr = parseaddr(row.sender or "")[1].strip().lower()
        if not addr:
            continue
        if _AUTO_REPLY_SUBJECT_RE.search(subject):
            # Out of office: not a reply (the sequence must not stop), but pause
            # this contact's follow-ups until they are back (FR-SEQ-04).
            from sqlalchemy import func as _f
            from sqlalchemy.orm.attributes import flag_modified
            from app.models.enterprise import CampaignContact as _CC
            back = _ooo_return_date(row.body or "", now)
            for c in db.query(_CC).filter(
                (_f.lower(_CC.email) == addr) | (_f.lower(_CC.alt_email) == addr),
                _CC.response_ts.is_(None),
            ).all():
                extra = dict(c.extra_json or {})
                extra["paused_until"] = back.isoformat()
                c.extra_json = extra
                flag_modified(c, "extra_json")
                recorded.append({"contact_ref": c.contact_ref, "campaign_id": str(c.campaign_id), "email": addr,
                                 "response_type": "out_of_office", "paused_until": back.date().isoformat(),
                                 "opted_out": False})
            continue
        contacts = db.query(CampaignContact).filter(
            (func.lower(CampaignContact.email) == addr) | (func.lower(CampaignContact.alt_email) == addr),
            CampaignContact.message_sent_ts.isnot(None),
            CampaignContact.response_ts.is_(None),
        ).all()
        if not contacts:
            continue

        reply = _new_reply_text(row.body)
        # Subject is "Re: <our subject>" — only classify the reply body, not our own words.
        response_type = _classify_keyword(reply)
        wants_out = bool(_OPTOUT_REPLY_RE.search(reply))
        if wants_out:
            response_type = "not_interested"

        for c in contacts:
            c.response_ts = now
            c.response_type = response_type
            snippet = reply[:300].replace("\n", " ")
            c.notes = ((c.notes + "\n") if c.notes else "") + f"[reply {now:%Y-%m-%d %H:%M}] {snippet}"
            recorded.append({"contact_ref": c.contact_ref, "campaign_id": str(c.campaign_id),
                             "email": addr, "response_type": response_type, "opted_out": wants_out})

        if wants_out:
            c0 = contacts[0]
            if db.get(ContactOptOut, c0.contact_ref) is None:
                db.add(ContactOptOut(contact_ref=c0.contact_ref, opted_out_at=now, email=addr,
                                     name=c0.name, msisdn=c0.msisdn, campaign_id=str(c0.campaign_id)))
        elif response_type != "not_interested":
            # A real reply that isn't a "no" — hand it to a person today.
            try:
                from app.models.enterprise import Campaign
                from app.services.campaign_intel import escalate_to_crm
                c0 = contacts[0]
                campaign = db.get(Campaign, c0.campaign_id)
                if campaign is not None:
                    escalate_to_crm(db, c0, campaign, "reply",
                                    f"Reply ({response_type or 'unclassified'}):\n{reply[:1500]}")
                    from app.agents.k5_response_tracker import needs_handoff
                    if needs_handoff(reply):
                        from app.core.config import settings as _s
                        from app.services.isr_queue import context_pack, create_task
                        create_task(
                            db, kind="handoff", contact_ref=c0.contact_ref,
                            account_id=(c0.extra_json or {}).get("account_id"), campaign_id=campaign.id,
                            owner_isr=_s.ism_presales_owner or None, priority="Highest",
                            subject=f"Pre-sales / quote: {c0.company or c0.name or addr} asked for details",
                            body=context_pack(db, c0, reply), source="handoff", email=c0.email,
                        )
            except Exception:
                import logging
                logging.getLogger(__name__).warning("reply escalation failed for %s", addr, exc_info=True)
    if recorded:
        db.commit()
    return recorded


def poll_pop3(db, limit: int = 25) -> list[EmailMessage]:
    if not configuration_status()["pop3_configured"]:
        raise RuntimeError("POP3 is not configured")

    client = poplib.POP3_SSL(settings.pop3_host, settings.pop3_port, timeout=30)
    try:
        client.user(settings.pop3_username)
        client.pass_(settings.pop3_password)
        uidls = client.uidl()[1][-limit:]
        received = []
        for row in uidls:
            number, uid = row.decode().split(maxsplit=1)
            if db.query(EmailMessage.id).filter_by(mailbox_uid=uid).first():
                continue
            lines = client.retr(int(number))[1]
            message = email.message_from_bytes(b"\n".join(lines))
            received.append(EmailMessage(
                mailbox_uid=uid,
                direction="in",
                status="received",
                sender=_text(message.get("From")),
                recipient=_text(message.get("To")),
                subject=_text(message.get("Subject")),
                body=_body(message),
                correlation_id=uuid.uuid4(),
            ))
        db.add_all(received)
        db.commit()
        process_bounce_notifications(db, received)
        process_campaign_replies(db, received)
        return received
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def send_approved_draft(db, draft: EmailMessage) -> EmailMessage:
    if not settings.mail_send_enabled:
        raise RuntimeError("SMTP delivery is disabled by MAIL_SEND_ENABLED")
    if not configuration_status()["smtp_configured"]:
        raise RuntimeError("SMTP is not configured")
    if draft.direction != "out" or draft.status != "pending_approval":
        raise RuntimeError("Only a pending outbound draft can be sent")

    message = RfcEmailMessage()
    message["From"] = settings.smtp_from or settings.smtp_username
    message["To"] = draft.recipient
    message["Subject"] = draft.subject or ""
    message["X-Yexis-Correlation-ID"] = str(draft.correlation_id)
    message["Message-ID"] = make_msgid(domain=settings.smtp_from.split("@")[-1] if "@" in (settings.smtp_from or "") else None)
    message.set_content(draft.body or "")

    for att in (draft.attachments or []):
        att_path = Path(att.get("path", ""))
        if not att_path.exists():
            continue
        raw = att_path.read_bytes()
        mime = att.get("mime_type") or mimetypes.guess_type(att_path.name)[0] or "application/octet-stream"
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(raw, maintype=maintype, subtype=subtype or "octet-stream", filename=att_path.name)
    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as client:
            client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        draft.status = "sent"
        draft.sent_at = datetime.now(timezone.utc)
        draft.provider_message_id = str(message["Message-ID"] or "")
    except Exception as exc:
        draft.status = "failed"
        draft.error = str(exc)
        raise
    finally:
        db.commit()
    return draft
