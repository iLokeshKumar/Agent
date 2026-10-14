"""Approval card renderer.

Produces two formats from an Approval row:

1. HTML email — a minimal table that renders correctly in Outlook and
   Gmail, with one-click Approve and Reject links signed with HMAC so
   the backend can trust them without a login.

2. WhatsApp interactive button — a WABA button message payload with two
   buttons (Approve / Reject) whose IDs encode the approval ID so the
   webhook handler can route the decision.

Neither format contains sensitive data beyond what is already in the
approval payload — evidence links point back to the backend, not to
third-party systems.
"""

import base64
import hashlib
import hmac
import textwrap
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from app.models.approval import Approval


# ── Token helpers ─────────────────────────────────────────────────────


def _make_callback_token(approval_id: uuid.UUID, action: str) -> str:
    """HMAC-signed token for one-click email links.

    Format: base64url(approval_id:action:hmac)
    """
    secret = settings.approval_hmac_secret or "dev-insecure-secret"
    msg = f"{approval_id}:{action}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]
    raw = f"{approval_id}:{action}:{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def verify_callback_token(token: str) -> tuple[uuid.UUID, str] | None:
    """Return (approval_id, action) if the token is valid, else None."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        approval_id_str, action, received_sig = raw.split(":", 2)
        approval_id = uuid.UUID(approval_id_str)
    except Exception:
        return None

    secret = settings.approval_hmac_secret or "dev-insecure-secret"
    msg = f"{approval_id}:{action}".encode()
    expected_sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected_sig, received_sig):
        return None
    if action not in ("approve", "reject"):
        return None
    return approval_id, action


# ── Summary builders ──────────────────────────────────────────────────


def _approval_summary(approval: "Approval") -> str:
    """One-line summary for notification bodies."""
    payload = approval.payload or {}
    parts = [approval.approval_type.replace("_", " ").title()]

    amount = payload.get("amount") or payload.get("total")
    if amount:
        parts.append(f"₹{amount:,.2f}" if isinstance(amount, (int, float)) else str(amount))

    description = (
        payload.get("description")
        or payload.get("item_description")
        or payload.get("vendor")
        or ""
    )
    if description:
        parts.append(textwrap.shorten(str(description), width=60, placeholder="…"))

    return " — ".join(parts)


def _payload_rows(payload: dict) -> list[tuple[str, str]]:
    """Flatten approval payload into display rows, skipping noise keys."""
    skip = {"raw", "channel", "sender", "items"}
    rows = []
    for k, v in payload.items():
        if k in skip or v is None:
            continue
        label = k.replace("_", " ").title()
        if isinstance(v, (int, float)) and k in ("amount", "total", "value"):
            value = f"₹{v:,.2f}"
        elif isinstance(v, list):
            value = f"{len(v)} items"
        else:
            value = textwrap.shorten(str(v), width=120, placeholder="…")
        rows.append((label, value))
    return rows[:12]  # cap at 12 rows


# ── HTML email renderer ───────────────────────────────────────────────


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 14px; color: #333; }}
  .card {{ max-width: 600px; margin: 24px auto; border: 1px solid #ddd;
           border-radius: 6px; overflow: hidden; }}
  .header {{ background: #1a3a5c; color: #fff; padding: 16px 24px; }}
  .header h2 {{ margin: 0; font-size: 18px; }}
  .header p {{ margin: 4px 0 0; font-size: 12px; opacity: 0.8; }}
  .body {{ padding: 20px 24px; }}
  table.details {{ width: 100%; border-collapse: collapse; margin-bottom: 16px; }}
  table.details td {{ padding: 6px 0; vertical-align: top; }}
  table.details td.label {{ color: #666; width: 38%; font-size: 13px; }}
  table.details td.value {{ font-weight: 500; }}
  .sla {{ font-size: 12px; color: #c0392b; margin-bottom: 16px; }}
  .actions {{ text-align: center; padding: 12px 0; }}
  .btn {{ display: inline-block; padding: 10px 28px; border-radius: 4px;
          font-size: 14px; font-weight: bold; text-decoration: none;
          margin: 0 8px; }}
  .btn-approve {{ background: #27ae60; color: #fff; }}
  .btn-reject  {{ background: #e74c3c; color: #fff; }}
  .footer {{ background: #f5f5f5; padding: 10px 24px;
             font-size: 11px; color: #999; text-align: center; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h2>Action Required: {approval_type}</h2>
    <p>Requested by {requested_by} on {requested_at}</p>
  </div>
  <div class="body">
    <table class="details">
{rows}
    </table>
    {sla_line}
    <div class="actions">
      <a class="btn btn-approve" href="{approve_url}">Approve</a>
      <a class="btn btn-reject"  href="{reject_url}">Reject</a>
    </div>
  </div>
  <div class="footer">
    Approval #{approval_id} &nbsp;|&nbsp;
    Reply within {sla_hours}h &nbsp;|&nbsp;
    Yexis Agent Platform
  </div>
</div>
</body>
</html>"""


def render_email_html(
    approval: "Approval",
    approve_url: str,
    reject_url: str,
    sla_hours: int,
) -> str:
    payload = approval.payload or {}
    rows_html = "\n".join(
        f'      <tr><td class="label">{label}</td>'
        f'<td class="value">{value}</td></tr>'
        for label, value in _payload_rows(payload)
    )
    sla_line = (
        f'<p class="sla">⏱ Please action within {sla_hours} hours.</p>'
    )
    requested_at = (
        approval.requested_at.strftime("%d %b %Y %H:%M UTC")
        if approval.requested_at else ""
    )
    return _HTML_TEMPLATE.format(
        approval_type=approval.approval_type.replace("_", " ").title(),
        requested_by=approval.requested_by or "system",
        requested_at=requested_at,
        rows=rows_html,
        sla_line=sla_line,
        approve_url=approve_url,
        reject_url=reject_url,
        approval_id=str(approval.id)[:8],
        sla_hours=sla_hours,
    )


def render_email_card(
    approval: "Approval",
    sla_hours: int | None = None,
) -> tuple[str, str]:
    """Return (subject, html_body) for the approval email.

    URLs include HMAC-signed tokens so no login is required.
    """
    sla_hours = sla_hours or settings.approval_sla_hours
    base = settings.approval_base_url.rstrip("/")
    approve_token = _make_callback_token(approval.id, "approve")
    reject_token = _make_callback_token(approval.id, "reject")
    approve_url = f"{base}/approvals/callback?token={approve_token}&by=email"
    reject_url = f"{base}/approvals/callback?token={reject_token}&by=email"

    subject = (
        f"[Action Required] {approval.approval_type.replace('_', ' ').title()} "
        f"— {_approval_summary(approval)}"
    )
    html = render_email_html(approval, approve_url, reject_url, sla_hours)
    return subject, html


# ── WhatsApp interactive button renderer ─────────────────────────────


def render_waba_buttons(approval: "Approval") -> dict:
    """Return the arguments for send_interactive_button().

    Returns: {"to": ..., "body_text": ..., "buttons": [...]}
    The caller supplies ``to`` (phone number); this function provides
    the message content.
    """
    summary = _approval_summary(approval)
    requested_at = (
        approval.requested_at.strftime("%d %b %Y %H:%M")
        if approval.requested_at else ""
    )
    body_text = (
        f"*Action Required*\n"
        f"{approval.approval_type.replace('_', ' ').title()}\n\n"
        f"{summary}\n\n"
        f"Requested by: {approval.requested_by or 'system'}\n"
        f"At: {requested_at} UTC\n\n"
        f"Please approve or reject below."
    )
    return {
        "body_text": body_text,
        "buttons": [
            {"id": f"approve_{approval.id}", "title": "Approve"},
            {"id": f"reject_{approval.id}",  "title": "Reject"},
        ],
    }
