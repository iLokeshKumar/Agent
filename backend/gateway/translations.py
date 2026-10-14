"""Webhook payload translators — provider JSON to task payloads.

The gateway receives whatever a provider sends; agents accept clean
contracts. Each translator owns that mapping for one source, so the
gateway core never grows per-provider branches and an agent's payload
schema is honoured in exactly one place.

A translator returns ``(task_type, payload)``. It must never invent
business data: text comes from the message, everything else is
explicit channel metadata.
"""

import hashlib
import json
import logging


logger = logging.getLogger(__name__)


def _extract_waba_text(payload: dict) -> tuple[str | None, dict]:
    """Pull message text + sender metadata from WABA webhook shapes.

    Handles the real BSP envelope (entry → changes → value → messages)
    as well as flat test payloads.
    """
    body = None
    sender = None

    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    body = (
                        message.get("text", {}).get("body")
                        or body
                    )
                    sender = (
                        message.get("from")
                        or value.get("contacts", [{}])[0].get(
                            "wa_id"
                        )
                        or sender
                    )
    except (AttributeError, TypeError):
        logger.warning("Unrecognised WABA envelope structure")

    if body is None:
        body = payload.get("text") or payload.get("body")

    return body, {"sender": sender}


def translate(source: str, payload: dict) -> tuple[str, dict]:
    """Map a webhook payload to (task_type, task_payload).

    Raises ValueError when nothing actionable can be extracted — the
    gateway then records the event as rejected rather than enqueueing
    a task destined for VALIDATION_ERROR.
    """
    if source == "waba":
        return _translate_waba(payload)

    if source == "zoho_books":
        # Books webhooks carry entity ids; F3 re-syncs from the API
        # rather than trusting webhook bodies — by reference only.
        return "books_to_tally", {
            "trigger": "webhook",
            "books_payload": payload,
        }

    if source == "graph":
        subject = payload.get("subject") or ""
        body_text = payload.get("body_preview") or ""
        description = subject or body_text
        if not description:
            raise ValueError("graph webhook carries no subject or body")
        return "purchase_request", {
            "items": [],
            "description": description,
            "channel": "email",
            "raw": {
                k: payload.get(k)
                for k in ("subject", "from", "body_preview")
            },
        }

    raise ValueError(f"no translator for source {source}")


def _extract_delivery_status(payload: dict) -> tuple[str, str] | None:
    """Extract (provider_msg_id, status) from a BSP delivery receipt webhook.

    Gallabox and Meta both use the same envelope shape with a ``statuses``
    array instead of ``messages``.  Returns None when the payload is not a
    delivery status update.
    """
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                statuses = change.get("value", {}).get("statuses", [])
                for s in statuses:
                    mid = s.get("id")
                    status = s.get("status")  # "sent"|"delivered"|"read"|"failed"
                    if mid and status:
                        return mid, status
    except (AttributeError, TypeError):
        pass
    # Flat test shape
    if payload.get("type") in ("status", "message_status"):
        mid = payload.get("message_id") or payload.get("id")
        status = payload.get("status")
        if mid and status:
            return mid, status
    return None


def _extract_button_reply(payload: dict) -> tuple[str | None, str | None]:
    """Extract button_reply id and sender from WABA interactive webhook."""
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for message in change.get("value", {}).get("messages", []):
                    if message.get("type") == "interactive":
                        interactive = message.get("interactive", {})
                        if interactive.get("type") == "button_reply":
                            button_id = interactive["button_reply"].get("id", "")
                            sender = message.get("from")
                            return button_id, sender
    except (AttributeError, TypeError, KeyError):
        pass
    # Flat test payloads
    if payload.get("type") == "button_reply":
        return payload.get("button_id"), payload.get("sender")
    return None, None


def _translate_waba(payload: dict) -> tuple[str, dict]:
    # Delivery receipt — not a message, just a status update.
    delivery = _extract_delivery_status(payload)
    if delivery:
        provider_msg_id, status = delivery
        return "delivery_receipt", {
            "provider_msg_id": provider_msg_id,
            "status": status,
        }

    # Check for interactive button reply first (approval decisions).
    button_id, sender = _extract_button_reply(payload)
    if button_id:
        # Button IDs are "approve_{uuid}" or "reject_{uuid}".
        if button_id.startswith("approve_") or button_id.startswith("reject_"):
            parts = button_id.split("_", 1)
            action, approval_id = parts[0], parts[1]
            return "approval_decision", {
                "approval_id": approval_id,
                "action": action,   # "approve" or "reject"
                "decided_by": sender or "whatsapp_user",
                "channel": "whatsapp",
            }
        raise ValueError(f"unrecognised button_reply id: {button_id!r}")

    body, meta = _extract_waba_text(payload)

    if not body or not body.strip():
        raise ValueError("WABA webhook carries no message text")

    text = body.strip()
    lower = text.lower()

    # Intent triage — service keywords route to V1, order keywords to
    # P1. Anything ambiguous stays with the service desk rather than a
    # wrong queue; humans see it via V1's validation output.
    SERVICE_KEYWORDS = (
        "not working", "repair", "broken", "fault", "issue",
        "service", "warranty", "amc", "problem", "error",
    )
    ORDER_KEYWORDS = ("order", "quote", "price", "purchase", "po ")

    if any(k in lower for k in ORDER_KEYWORDS) and not any(
        k in lower for k in SERVICE_KEYWORDS
    ):
        return "purchase_request", {
            "description": text,
            "channel": "whatsapp",
            "sender": meta.get("sender"),
        }

    return "service_request", {
        "request_type": "service_request",
        "description": text,
        "channel": "whatsapp",
        "sender": meta.get("sender"),
    }
