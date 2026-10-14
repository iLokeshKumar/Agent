"""Approval card delivery service.

Sends approval cards to the approver's channel of choice (email or
WhatsApp) and tracks delivery. Exposes ``check_and_escalate_sla()``
for the scheduler to call on a cadence, which re-delivers overdue
pending approvals to a configured escalation recipient.
"""

import logging
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.approval import Approval
from app.services.card_renderer import render_email_card, render_waba_buttons

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ── Email delivery ────────────────────────────────────────────────────


def _send_email(to: str, subject: str, html_body: str) -> None:
    """Send an HTML email via the configured SMTP server.

    Raises RuntimeError when SMTP is not configured or delivery is
    disabled — the caller decides whether to surface this to the user.
    """
    if not settings.mail_send_enabled:
        raise RuntimeError(
            "SMTP delivery disabled — set MAIL_SEND_ENABLED=true in .env"
        )
    if not (settings.smtp_host and settings.smtp_username and settings.smtp_password):
        raise RuntimeError("SMTP not configured — set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD")

    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_username
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(
        domain=settings.smtp_from.split("@")[-1]
        if "@" in (settings.smtp_from or "")
        else "yexis.local"
    )
    msg.set_content("Please enable HTML to view this approval card.")
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as client:
        client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(msg)

    logger.info("Approval card email sent to %s", to)


# ── WhatsApp delivery ─────────────────────────────────────────────────


def _send_whatsapp(phone: str, approval: Approval) -> None:
    from mcp_servers.whatsapp.server import WhatsAppError, send_interactive_button

    card = render_waba_buttons(approval)
    try:
        send_interactive_button(
            to=phone,
            body_text=card["body_text"],
            buttons=card["buttons"],
        )
    except WhatsAppError as exc:
        raise RuntimeError(f"WhatsApp delivery failed: {exc}") from exc

    logger.info("Approval card WhatsApp sent to %s", phone)


# ── Public API ────────────────────────────────────────────────────────


def deliver_card(
    approval: Approval,
    channel: str,
    recipient: str,
    db: Session,
    sla_hours: int | None = None,
) -> None:
    """Send an approval card to the given recipient and record delivery.

    channel: "email" | "whatsapp"
    recipient: email address or E.164 phone number (no +)

    Raises RuntimeError on delivery failure — the caller records the
    error; the approval row is NOT marked as delivered until success.
    """
    if approval.status != "PENDING":
        raise ValueError(
            f"Approval {approval.id} is {approval.status}, cannot deliver"
        )

    sla_h = sla_hours or settings.approval_sla_hours

    if channel == "email":
        subject, html = render_email_card(approval, sla_hours=sla_h)
        _send_email(recipient, subject, html)
    elif channel == "whatsapp":
        _send_whatsapp(recipient, approval)
    else:
        raise ValueError(f"Unknown delivery channel {channel!r}")

    now = datetime.now(timezone.utc)
    approval.delivered_at = now
    approval.delivery_channel = channel
    approval.delivery_recipient = recipient
    approval.sla_deadline = now + timedelta(hours=sla_h)
    approval.escalated = False
    db.commit()
    logger.info(
        "Approval %s delivered via %s to %s; SLA deadline %s",
        approval.id,
        channel,
        recipient,
        approval.sla_deadline.isoformat(),
    )


def check_and_escalate_sla(
    db: Session,
    escalation_email: str | None = None,
    escalation_phone: str | None = None,
) -> int:
    """Scan pending approvals past their SLA deadline and escalate.

    Escalation re-delivers the card to the escalation recipient (if
    configured) and marks the approval row as escalated so it is not
    re-sent on every tick.

    Returns the count of approvals escalated this run.
    """
    now = datetime.now(timezone.utc)
    overdue = db.execute(
        select(Approval).where(
            Approval.status == "PENDING",
            Approval.sla_deadline <= now,
            Approval.escalated == False,  # noqa: E712
        )
    ).scalars().all()

    escalated_count = 0
    for approval in overdue:
        escalated = False

        if escalation_email:
            try:
                subject, html = render_email_card(approval, sla_hours=0)
                subject = f"[ESCALATION] {subject}"
                _send_email(escalation_email, subject, html)
                escalated = True
            except Exception as exc:
                logger.error(
                    "SLA escalation email failed for approval %s: %s",
                    approval.id,
                    exc,
                )

        if escalation_phone:
            try:
                _send_whatsapp(escalation_phone, approval)
                escalated = True
            except Exception as exc:
                logger.error(
                    "SLA escalation WhatsApp failed for approval %s: %s",
                    approval.id,
                    exc,
                )

        if escalated:
            approval.escalated = True
            escalated_count += 1
            logger.warning(
                "Approval %s escalated (SLA deadline was %s)",
                approval.id,
                approval.sla_deadline,
            )

    if escalated_count:
        db.commit()

    return escalated_count
