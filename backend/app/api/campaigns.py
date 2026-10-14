"""Campaign API — UI-friendly campaign management + opt-out/tracking.

GET  /campaigns                             — list all campaigns
POST /campaigns                             — create campaign brief (K1)
POST /campaigns/{id}/audience               — build audience (K2)
POST /campaigns/{id}/send                   — broadcast messages (K3)
GET  /campaigns/{id}/analytics              — compute + return KPIs (K6)
GET  /campaigns/unsubscribe?t=<token>       — dealer self-service opt-out
POST /campaigns/{campaign_id}/schedule      — schedule/reschedule broadcast
GET  /campaigns/{campaign_id}/report        — K3 report shortcut
GET  /campaigns/{campaign_id}/engagement    — detailed open/click log
GET  /campaigns/track/open?t=<token>        — 1x1 pixel, logs an open
GET  /campaigns/track/click?t=<token>&u=... — logs a click, redirects
POST /campaigns/transactional/send          — one-off event-triggered email
"""

import base64
import datetime
import hashlib
import hmac
import logging
import os
import tempfile
import urllib.parse
import uuid as _uuid_mod

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# ── UI management endpoints ────────────────────────────────────────────────────

@router.get("")
def list_campaigns(
    response: Response,
    entity: str = "YEXIS-EL",
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List all campaigns with audience count and KPI summary."""
    from app.models.enterprise import Campaign, CampaignContact
    from sqlalchemy import func

    query = db.query(Campaign).filter(Campaign.entity == entity)
    response.headers["X-Total-Count"] = str(query.count())
    campaigns = query.order_by(
        Campaign.created_ts.desc(), Campaign.id.desc()
    ).offset(max(0, offset)).limit(max(1, min(limit, 200))).all()

    # Audience counts in one query
    counts = dict(
        db.query(CampaignContact.campaign_id, func.count())
        .filter(CampaignContact.campaign_id.in_([c.id for c in campaigns]))
        .group_by(CampaignContact.campaign_id)
        .all()
    )
    sent_counts = dict(
        db.query(CampaignContact.campaign_id, func.count())
        .filter(
            CampaignContact.campaign_id.in_([c.id for c in campaigns]),
            CampaignContact.message_sent_ts.isnot(None),
        )
        .group_by(CampaignContact.campaign_id)
        .all()
    )

    return [
        {
            "campaign_id": str(c.id),
            "campaign_ref": c.campaign_ref,
            "name": c.name,
            "campaign_type": c.campaign_type,
            "entity": c.entity,
            "status": c.status,
            "start_date": str(c.start_date) if c.start_date else None,
            "end_date": str(c.end_date) if c.end_date else None,
            "budget": c.budget,
            "default_channel": c.default_channel,
            "message_template_id": c.message_template_id,
            "target_skus": (c.target_skus_json or {}).get("skus", []),
            "created_ts": c.created_ts.isoformat() if c.created_ts else None,
            "audience_count": counts.get(c.id, 0),
            "sent_count": sent_counts.get(c.id, 0),
            "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
            "kpi": c.kpi_json or {},
        }
        for c in campaigns
    ]


@router.post("")
def create_campaign(body: dict, db: Session = Depends(get_db)):
    """Create a campaign brief via K1. Returns campaign_id immediately."""
    from app.agents.k1_campaign_brief import K1CampaignBrief
    agent = K1CampaignBrief()
    payload = {"action": "create", **body}
    err = agent.validate(payload)
    if err:
        raise HTTPException(400, err)
    result = agent.execute(payload, requested_by=body.get("requested_by", "ui"))
    if result.status != "SUCCESS":
        raise HTTPException(422, result.error or "Campaign creation failed")
    return result.output


@router.patch("/{campaign_id}")
def update_campaign(campaign_id: str, body: dict, db: Session = Depends(get_db)):
    """Update a campaign brief while it is still a draft."""
    from datetime import date
    from app.agents.k1_campaign_brief import (
        VALID_CAMPAIGN_TYPES, VALID_CHANNELS, VALID_ENTITIES,
    )
    from app.models.enterprise import Campaign

    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    if campaign.status != "draft":
        raise HTTPException(409, "Only draft campaigns can be edited")

    allowed = {
        "name", "campaign_type", "entity", "start_date", "end_date",
        "budget", "target_skus", "message_template_id", "default_channel",
    }
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(400, f"Unsupported fields: {', '.join(sorted(unknown))}")

    if "name" in body:
        name = str(body["name"]).strip()
        if not name:
            raise HTTPException(400, "name cannot be empty")
        campaign.name = name
    if "campaign_type" in body:
        if body["campaign_type"] not in VALID_CAMPAIGN_TYPES:
            raise HTTPException(400, f"campaign_type must be one of {sorted(VALID_CAMPAIGN_TYPES)}")
        campaign.campaign_type = body["campaign_type"]
    if "entity" in body:
        if body["entity"] not in VALID_ENTITIES:
            raise HTTPException(400, f"entity must be one of {sorted(VALID_ENTITIES)}")
        campaign.entity = body["entity"]
    for field in ("start_date", "end_date"):
        if field in body:
            try:
                setattr(campaign, field, date.fromisoformat(body[field]) if body[field] else None)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{field} must be an ISO date (YYYY-MM-DD)")
    if campaign.start_date and campaign.end_date and campaign.end_date < campaign.start_date:
        raise HTTPException(400, "end_date must be on or after start_date")
    if "budget" in body:
        try:
            budget = float(body["budget"]) if body["budget"] not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(400, "budget must be a number")
        if budget is not None and budget < 0:
            raise HTTPException(400, "budget cannot be negative")
        campaign.budget = budget
    if "target_skus" in body:
        skus = body["target_skus"] or []
        if not isinstance(skus, list):
            raise HTTPException(400, "target_skus must be a list")
        campaign.target_skus_json = {"skus": [str(s).strip() for s in skus if str(s).strip()]}
    if "message_template_id" in body:
        campaign.message_template_id = str(body["message_template_id"]).strip() or None
    if "default_channel" in body:
        if body["default_channel"] not in VALID_CHANNELS:
            raise HTTPException(400, f"default_channel must be one of {sorted(VALID_CHANNELS)}")
        campaign.default_channel = body["default_channel"]

    db.commit()
    db.refresh(campaign)
    return {"campaign_id": str(campaign.id), "name": campaign.name, "status": campaign.status}


@router.delete("/{campaign_id}")
def delete_campaign(campaign_id: str, db: Session = Depends(get_db)):
    """Delete an unsent draft and its draft audience rows."""
    from app.models.enterprise import Campaign, CampaignContact, CampaignSequenceStep

    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    if campaign.status != "draft":
        raise HTTPException(409, "Only draft campaigns can be deleted")
    sent_count = db.query(CampaignContact).filter(
        CampaignContact.campaign_id == campaign.id,
        CampaignContact.message_sent_ts.isnot(None),
    ).count()
    if sent_count:
        raise HTTPException(409, "A campaign with sent messages cannot be deleted")

    db.query(CampaignSequenceStep).filter(
        CampaignSequenceStep.campaign_id == campaign.id
    ).delete(synchronize_session=False)
    db.query(CampaignContact).filter(
        CampaignContact.campaign_id == campaign.id
    ).delete(synchronize_session=False)
    db.delete(campaign)
    db.commit()
    return {"campaign_id": campaign_id, "deleted": True}


@router.post("/{campaign_id}/audience")
def build_audience(campaign_id: str, body: dict = None, db: Session = Depends(get_db)):
    """Build campaign audience via K2. Runs synchronously — may take a few seconds."""
    from app.agents.k2_audience_builder import K2AudienceBuilder
    agent = K2AudienceBuilder()
    payload = {"action": "build", "campaign_id": campaign_id, **(body or {})}
    err = agent.validate(payload)
    if err:
        raise HTTPException(400, err)
    result = agent.execute(payload, requested_by=(body or {}).get("requested_by", "ui"))
    if result.status not in ("SUCCESS", "PARTIAL"):
        raise HTTPException(422, result.error or "Audience build failed")
    return result.output


def _suffix_for(filename: str) -> str:
    low = filename.lower()
    if low.endswith(".csv"):
        return ".csv"
    if low.endswith(".xls"):
        return ".xls"
    return ".xlsx"


@router.post("/{campaign_id}/contacts/preview")
async def preview_contacts_excel(
    campaign_id: str,
    file: UploadFile = File(...),
    sheet: str = Form(None),
    db: Session = Depends(get_db),
):
    """Inspect an uploaded file WITHOUT importing it — powers the column
    mapping screen. Returns detected columns with auto-guessed targets, sample
    values, the chosen sheet name, and a list of all available sheets.

    sheet: optional — when omitted the sheet most suitable for contacts is
    auto-selected. Pass a sheet name to see a different sheet's mapping.

    Nothing is written to the database. The browser keeps the file and
    re-sends it (with the confirmed mapping + sheet) to POST .../contacts/upload.
    """
    from app.agents.k2_audience_builder import parse_excel_preview
    from app.models.enterprise import Campaign

    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, f"Campaign '{campaign_id}' not found")

    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(400, "Only .xlsx, .xls, or .csv files are accepted")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=_suffix_for(filename))
    try:
        tmp.write(await file.read())
        tmp.close()
        preview, err = parse_excel_preview(tmp.name, sheet=sheet or None)
    finally:
        os.unlink(tmp.name)

    if err:
        raise HTTPException(422, f"Could not read file: {err}")

    preview["target_options"] = [
        {"value": "name", "label": "Full name"},
        {"value": "first_name", "label": "First name"},
        {"value": "email", "label": "Email"},
        {"value": "phone", "label": "Phone / mobile"},
        {"value": "company", "label": "Company / account"},
        {"value": "__custom__", "label": "Custom variable…"},
        {"value": "ignore", "label": "Ignore this column"},
    ]
    preview["filename"] = filename
    return preview


@router.post("/{campaign_id}/contacts/upload")
async def upload_contacts_excel(
    campaign_id: str,
    file: UploadFile = File(...),
    mapping: str = Form(None),
    sheet: str = Form(None),
    db: Session = Depends(get_db),
):
    """Import an Excel/CSV file into a campaign's audience.

    mapping: optional JSON string, {column_index: target}, produced by the
    mapping screen (see POST .../contacts/preview). Targets: name |
    first_name | email | phone | company | ignore | <custom variable name>.
    When omitted, every column's target is auto-guessed from its header
    (backward-compatible with the old one-click upload).

    Multiple email columns → email (primary), alt_email, email_3…
    Multiple phone columns → msisdn (primary), phone_2, phone_3…

    Any row missing both email and phone is skipped; duplicates (same
    contact_ref) are deduplicated automatically.

    Returns: { added, skipped_duplicate, skipped_no_contact, total_audience }
    """
    import json as _json
    from app.agents.k2_audience_builder import _fetch_excel, _normalise_phone
    from app.models.enterprise import Campaign, CampaignContact

    # Validate campaign exists
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, f"Campaign '{campaign_id}' not found")
    if campaign.status not in ("draft", "audience_built"):
        raise HTTPException(400, f"Campaign must be in draft/audience_built to upload contacts (current: {campaign.status})")

    # Validate file type
    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(400, "Only .xlsx, .xls, or .csv files are accepted")

    # Parse the optional column mapping produced by the mapping screen.
    mapping_dict = None
    if mapping:
        try:
            parsed = _json.loads(mapping)
            if isinstance(parsed, dict) and parsed:
                mapping_dict = parsed
        except (ValueError, TypeError):
            raise HTTPException(400, "mapping must be a valid JSON object")

    # Save to a temp file (openpyxl / csv reader needs a real file path)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=_suffix_for(filename))
    try:
        contents = await file.read()
        tmp.write(contents)
        tmp.close()

        contacts, err = _fetch_excel(tmp.name, mapping=mapping_dict, sheet=sheet or None)
    finally:
        os.unlink(tmp.name)

    if err and not contacts:
        raise HTTPException(422, f"Could not parse Excel: {err}")

    added = skipped_dup = skipped_no_contact = 0
    seen_contacts: dict[str, CampaignContact] = {}
    for c in contacts:
        if not c.get("email") and not c.get("msisdn"):
            skipped_no_contact += 1
            continue
        contact_ref = str(c.get("contact_ref") or "").strip()
        if not contact_ref:
            skipped_no_contact += 1
            continue

        # The same address can occur multiple times in one spreadsheet. Rows
        # added with db.add() are not visible to the following SELECT until a
        # flush, so track this batch separately from rows already in the DB.
        if contact_ref in seen_contacts:
            target = seen_contacts[contact_ref]
            if c.get("msisdn") and not target.msisdn:
                target.msisdn = c["msisdn"]
            if c.get("email") and not target.email:
                target.email = c["email"]
            if c.get("alt_email") and not target.alt_email:
                target.alt_email = c["alt_email"]
            if c.get("name") and not target.name:
                target.name = c["name"]
            if c.get("company") and not target.company:
                target.company = c["company"]
            if c.get("extra") and not target.extra_json:
                target.extra_json = c["extra"]
            skipped_dup += 1
            continue

        existing = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.contact_ref == contact_ref,
        ).first()
        if existing:
            # Update phone/email if we have better data
            if c.get("msisdn") and not existing.msisdn:
                existing.msisdn = c["msisdn"]
            if c.get("email") and not existing.email:
                existing.email = c["email"]
            if c.get("alt_email") and not existing.alt_email:
                existing.alt_email = c["alt_email"]
            if c.get("name") and not existing.name:
                existing.name = c["name"]
            if c.get("company") and not existing.company:
                existing.company = c["company"]
            if c.get("extra") and not existing.extra_json:
                existing.extra_json = c["extra"]
            seen_contacts[contact_ref] = existing
            skipped_dup += 1
            continue

        contact = CampaignContact(
            campaign_id=campaign.id,
            contact_ref=contact_ref,
            contact_type="lead",
            msisdn=c.get("msisdn"),
            email=c.get("email"),
            alt_email=c.get("alt_email") or None,
            name=c.get("name") or None,
            company=c.get("company") or None,
            extra_json=c.get("extra") or None,
        )
        db.add(contact)
        seen_contacts[contact_ref] = contact
        added += 1

    campaign.status = "audience_built"
    db.commit()

    total = db.query(CampaignContact).filter(
        CampaignContact.campaign_id == campaign.id
    ).count()

    return {
        "campaign_id": campaign_id,
        "campaign_name": campaign.name,
        "filename": filename,
        "rows_in_file": len(contacts),
        "added": added,
        "skipped_duplicate": skipped_dup,
        "skipped_no_contact": skipped_no_contact,
        "total_audience": total,
        "parse_warning": err if err else None,
        "next_step": "Run POST /campaigns/{id}/send with a template_id to broadcast.",
    }


@router.post("/{campaign_id}/send")
def send_broadcast(campaign_id: str, body: dict = None, db: Session = Depends(get_db)):
    """Trigger campaign broadcast via K3."""
    from app.agents.k3_broadcast import K3Broadcast
    agent = K3Broadcast()
    payload = {"action": "send", "campaign_id": campaign_id, **(body or {})}
    err = agent.validate(payload)
    if err:
        raise HTTPException(400, err)
    result = agent.execute(payload, requested_by=(body or {}).get("requested_by", "ui"))
    if result.status not in ("SUCCESS", "PARTIAL"):
        raise HTTPException(422, result.error or "Broadcast failed")
    return result.output


@router.post("/{campaign_id}/test-send")
def send_test_email(campaign_id: str, body: dict, db: Session = Depends(get_db)):
    """Send ONE test email so you can see exactly how the campaign will land.

    Renders through the same personalization path as a broadcast, using an
    existing campaign contact, and sends only to the address you specify.

    It is deliberately harmless: it does NOT mark anyone as sent, does NOT count
    toward campaign analytics and does not add campaign tracking or unsubscribe links. Works even while
    the template is still 'pending', so you can preview before approving.

    Body: { "to_email": "you@example.com", "template_id"?: "...", "contact_ref"?: "..." }
    """
    from app.agents import k3_broadcast as k3
    from app.models.enterprise import Campaign, CampaignContact, TemplateRegistry

    to_email = (body or {}).get("to_email", "").strip()
    if not to_email or "@" not in to_email:
        raise HTTPException(400, "A valid 'to_email' is required.")

    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")

    template_id = (body or {}).get("template_id") or campaign.message_template_id
    if not template_id:
        raise HTTPException(400, "Choose a template (or set one on the campaign) before sending a test.")
    template = db.query(TemplateRegistry).filter(
        TemplateRegistry.template_id == template_id
    ).first()
    if template is None:
        raise HTTPException(404, f"Template '{template_id}' not found.")
    if template.channel != "email":
        raise HTTPException(400, f"Test email needs an email template — '{template_id}' is {template.channel}.")

    contact = None
    ref = (body or {}).get("contact_ref")
    if ref:
        contact = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.contact_ref == ref,
        ).first()
        if contact is None:
            raise HTTPException(404, "Campaign contact not found.")
    else:
        contact = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id
        ).first()
    if contact is None:
        raise HTTPException(
            409,
            "Add a real contact to the campaign audience before sending a test email.",
        )

    body_html = k3._resolve_body(template, contact, campaign, db=db)
    subject = "[TEST] " + k3._resolve_subject(template, contact, campaign)

    try:
        k3._send_smtp_email(
            to_email, subject, body_html,
            reply_to=getattr(template, "reply_to", None),
            attachments=getattr(template, "attachments_json", None),
            pace=False,
        )
    except Exception as exc:
        logger.warning("Test email to %s failed: %s", to_email, exc)
        raise HTTPException(422, f"Test send failed: {exc}")

    return {
        "sent": True,
        "to_email": to_email,
        "subject": subject,
        "template_id": template_id,
        "contact_ref": contact.contact_ref,
    }


@router.get("/{campaign_id}/analytics")
def campaign_analytics(campaign_id: str, db: Session = Depends(get_db)):
    """Compute and return KPIs for a campaign via K6."""
    from app.agents.k6_campaign_analytics import K6CampaignAnalytics
    agent = K6CampaignAnalytics()
    result = agent.execute(
        {"action": "compute", "campaign_id": campaign_id},
        requested_by="ui",
    )
    if result.status != "SUCCESS":
        raise HTTPException(404, result.error or "Campaign not found")
    return result.output


# 1x1 transparent GIF served for every open-tracking pixel hit.
_TRANSPARENT_PIXEL = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)


def _verify_unsub_token(token_str: str) -> tuple[str, str] | None:
    """Verify HMAC-signed unsubscribe token.

    Token format: contact_ref:campaign_id:sig (URL-encoded).
    Returns (contact_ref, campaign_id) on success, None if invalid.
    """
    try:
        decoded = urllib.parse.unquote(token_str)
        parts = decoded.rsplit(":", 1)
        if len(parts) != 2:
            return None
        data_part, sig = parts
        secret = settings.approval_hmac_secret or "dev-secret"
        expected = hmac.new(secret.encode(), data_part.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(expected, sig):
            return None
        contact_ref, campaign_id = data_part.split(":", 1)
        return contact_ref, campaign_id
    except Exception:
        return None


def _verify_tracking_token(token_str: str) -> tuple[str | None, str, str | None] | None:
    """Verify HMAC-signed open/click tracking token.

    Token format: message_log_id:contact_ref:campaign_id:sig (URL-encoded).
    message_log_id/campaign_id may be empty strings if unavailable at send
    time. Returns (message_log_id, contact_ref, campaign_id) or None.
    """
    try:
        decoded = urllib.parse.unquote(token_str)
        data_part, sig = decoded.rsplit(":", 1)
        secret = settings.approval_hmac_secret or "dev-secret"
        expected = hmac.new(secret.encode(), data_part.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(expected, sig):
            return None
        message_log_id, contact_ref, campaign_id = data_part.split(":", 2)
        return (message_log_id or None), contact_ref, (campaign_id or None)
    except Exception:
        return None


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/track/open")
def track_open(t: str, request: Request, db: Session = Depends(get_db)):
    """1x1 pixel embedded in every campaign email. Recorded as an 'open'
    the moment a mail client fetches it — see GET /campaigns/{id}/engagement
    for the detailed per-contact log this feeds.

    Always returns the pixel, even on a bad/expired token, so a broken
    tracking link never shows as a broken image in the recipient's inbox.
    """
    from app.models.enterprise import EmailEngagementEvent

    parsed = _verify_tracking_token(t)
    if parsed:
        message_log_id, contact_ref, campaign_id = parsed
        try:
            db.add(EmailEngagementEvent(
                message_log_id=_uuid_mod.UUID(message_log_id) if message_log_id else None,
                campaign_id=_uuid_mod.UUID(campaign_id) if campaign_id else None,
                contact_ref=contact_ref,
                event_type="open",
                user_agent=request.headers.get("user-agent"),
                ip_address=_client_ip(request),
            ))
            db.commit()
        except Exception:
            logger.exception("Failed to record open event for contact_ref=%s", contact_ref)

    return Response(
        content=_TRANSPARENT_PIXEL,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@router.get("/track/click")
def track_click(t: str, u: str, request: Request, db: Session = Depends(get_db)):
    """Tracked-link redirect embedded in place of every href in a campaign
    email body. Logs the click (with which URL was clicked) then 302s to
    the real destination.

    Redirects even on a bad/expired token — a dead tracking link must
    never break a real link the recipient is trying to follow.
    """
    from app.models.enterprise import EmailEngagementEvent

    target = urllib.parse.unquote(u)
    if not target.startswith(("http://", "https://")):
        raise HTTPException(400, "Invalid redirect target")

    parsed = _verify_tracking_token(t)
    if parsed:
        message_log_id, contact_ref, campaign_id = parsed
        try:
            db.add(EmailEngagementEvent(
                message_log_id=_uuid_mod.UUID(message_log_id) if message_log_id else None,
                campaign_id=_uuid_mod.UUID(campaign_id) if campaign_id else None,
                contact_ref=contact_ref,
                event_type="click",
                url=target,
                user_agent=request.headers.get("user-agent"),
                ip_address=_client_ip(request),
            ))
            db.commit()
        except Exception:
            logger.exception("Failed to record click event for contact_ref=%s", contact_ref)

    return RedirectResponse(url=target, status_code=302)


@router.get("/{campaign_id}/scores")
def campaign_scores(campaign_id: str, tier: str | None = None, db: Session = Depends(get_db)):
    """Engagement score per contact, highest first — tier=hot is the call list.
    Scoring: 1 open=1, 2 opens=3, 3+ opens=5; click=5 (max 10); reply=10;
    positive reply +5. hot >= K3_HOT_SCORE_THRESHOLD, warm >= 2, else cold."""
    from app.models.enterprise import Campaign
    from app.services.campaign_intel import contact_scores, is_cold_stop
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    rows = sorted(contact_scores(db, campaign).values(), key=lambda r: -r["score"])
    for r in rows:
        r["cold_stopped"] = is_cold_stop(r)
    if tier:
        rows = [r for r in rows if r["tier"] == tier]
    summary = {t: sum(1 for r in rows if r["tier"] == t) for t in ("hot", "warm", "cold", "not_interested")}
    return {"campaign_id": campaign_id, "summary": summary, "contacts": rows}


@router.get("/{campaign_id}/engagement")
def campaign_engagement(
    campaign_id: str,
    event_type: str | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """Detailed open/click log for a campaign — per contact, per link,
    not just an aggregate rate.
    """
    from app.models.enterprise import EmailEngagementEvent

    try:
        cid = _uuid_mod.UUID(campaign_id)
    except ValueError:
        raise HTTPException(400, "campaign_id must be a UUID")

    query = select(EmailEngagementEvent).where(
        EmailEngagementEvent.campaign_id == cid
    ).order_by(EmailEngagementEvent.ts.desc())
    if event_type:
        query = query.where(EmailEngagementEvent.event_type == event_type)
    rows = db.execute(query.limit(max(1, min(limit, 2000)))).scalars().all()

    opens = [r for r in rows if r.event_type == "open"]
    clicks = [r for r in rows if r.event_type == "click"]
    return {
        "campaign_id": campaign_id,
        "summary": {
            "total_opens": len(opens),
            "unique_opens": len({r.contact_ref for r in opens}),
            "total_clicks": len(clicks),
            "unique_clicks": len({r.contact_ref for r in clicks}),
            "clicked_urls": sorted({r.url for r in clicks if r.url}),
        },
        "events": [
            {
                "contact_ref": r.contact_ref,
                "event_type": r.event_type,
                "url": r.url,
                "ts": r.ts,
                "user_agent": r.user_agent,
                "ip_address": r.ip_address,
            }
            for r in rows
        ],
    }


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(t: str, db: Session = Depends(get_db)):
    """Self-service opt-out portal linked in campaign messages.

    Verifies the signed token, marks the WaContact opt_in_status='opt_out',
    and returns a confirmation page. The link is single-use safe — repeated
    calls are idempotent.
    """
    from datetime import timezone as _tz
    from app.models.enterprise import WaContact, CampaignContact, ContactOptOut

    parsed = _verify_unsub_token(t)
    if parsed is None:
        raise HTTPException(400, "Invalid or expired unsubscribe link.")

    contact_ref, campaign_id = parsed

    # Pull contact details for the opt-out record
    cc = db.execute(
        select(CampaignContact).where(CampaignContact.contact_ref == contact_ref)
    ).scalars().first()

    # Upsert into ContactOptOut — the authoritative cross-channel suppression list
    existing = db.get(ContactOptOut, contact_ref)
    if not existing:
        db.add(ContactOptOut(
            contact_ref=contact_ref,
            opted_out_at=datetime.now(_tz.utc),
            email=cc.email if cc else None,
            name=cc.name if cc else None,
            msisdn=cc.msisdn if cc else None,
            campaign_id=campaign_id,
        ))

    # Also mark WaContact if one exists (keeps WA BSP layer consistent)
    wa = db.execute(
        select(WaContact).where(WaContact.contact_ref == contact_ref)
    ).scalars().first()
    if wa:
        wa.opt_in_status = "opt_out"

    db.commit()
    logger.info("Unsubscribe: contact %s opted out (campaign %s)", contact_ref, campaign_id)
    return HTMLResponse(content=_unsub_page(contact_ref), status_code=200)


@router.post("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_one_click(t: str, db: Session = Depends(get_db)):
    """RFC 8058 one-click unsubscribe. Gmail/Yahoo's "Unsubscribe" button
    POSTs to the List-Unsubscribe URL (body: List-Unsubscribe=One-Click)
    instead of opening it — without this handler that click got a 405
    and the contact kept receiving mail."""
    return unsubscribe(t=t, db=db)


@router.post("/{campaign_id}/schedule")
def schedule_campaign(
    campaign_id: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """Schedule a campaign broadcast for a specific UTC datetime.

    Body: {"template_id": "...", "scheduled_at": "2026-10-01T09:00:00Z"}
    """
    import uuid as _uuid
    from app.agents.k3_broadcast import K3Broadcast
    agent = K3Broadcast()
    payload = {
        "action": "schedule",
        "campaign_id": campaign_id,
        "template_id": body.get("template_id", ""),
        "scheduled_at": body.get("scheduled_at", ""),
        "channel": body.get("channel", "auto"),
    }
    err = agent.validate(payload)
    if err:
        raise HTTPException(400, err)
    result = agent.execute(payload, requested_by="api")
    if result.status != "SUCCESS":
        raise HTTPException(422, result.error or "Schedule failed")
    return result.output


@router.post("/transactional/send")
def send_transactional(body: dict, db: Session = Depends(get_db)):
    """Trigger a one-off, event-triggered email — outside the campaign/
    broadcast/trickle machinery, sent immediately.

    Body: {
      "template_id": "...",           # required — must be an email template
      "to_email": "...",              # required
      "event_type": "quote_approved", # optional — free text, shows in logs
      "contact_ref": "...",           # optional — for live-data lookups;
                                       # defaults to the local-part of to_email
      "contact_type": "dealer",       # optional — for segment overrides
      "campaign_name": "...",         # optional — cosmetic, used in {{campaign_name}}
      "context": {"order_no": "..."}  # optional — extra {{field}} values for
                                       # this send, merged over live data
    }
    """
    from app.agents.k3_broadcast import K3Broadcast
    agent = K3Broadcast()
    payload = {"action": "send_transactional", **body}
    err = agent.validate(payload)
    if err:
        raise HTTPException(400, err)
    result = agent.execute(payload, requested_by="api")
    if result.status not in ("SUCCESS",):
        raise HTTPException(422, result.error or "Transactional send failed")
    return result.output


@router.get("/{campaign_id}/contacts")
def list_campaign_contacts(
    campaign_id: str,
    status: str = "all",   # all | sent | pending | failed
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List contacts enrolled in a campaign with their send status.

    status filters:
      all     — every contact
      sent    — delivered (message_sent_ts set, not bounce-suppressed)
      pending — not yet attempted
      failed  — bounce-suppressed OR SMTP dispatch failure
    """
    from sqlalchemy import or_
    from app.models.enterprise import CampaignContact, EmailBounceEvent, MessageLog

    # ── Contacts that had a dispatch failure (MessageLog status=failed) ──
    failed_log_rows = db.query(MessageLog.to_ref).filter(
        MessageLog.status == "failed",
        MessageLog.correlation_type == "campaign",
        MessageLog.correlation_ref == campaign_id,
        MessageLog.to_ref.isnot(None),
    ).all()
    dispatch_failed_refs: set[str] = {r.to_ref for r in failed_log_rows}

    q = db.query(CampaignContact).filter(CampaignContact.campaign_id == campaign_id)

    if status == "sent":
        # Truly delivered — has a sent timestamp AND was not bounce-suppressed
        q = q.filter(
            CampaignContact.message_sent_ts.isnot(None),
            or_(
                CampaignContact.notes.is_(None),
                ~CampaignContact.notes.contains("[K3] Skipped"),
            ),
        )
    elif status == "pending":
        # Not yet attempted, and not a known dispatch failure
        q = q.filter(CampaignContact.message_sent_ts.is_(None))
        if dispatch_failed_refs:
            q = q.filter(CampaignContact.contact_ref.notin_(dispatch_failed_refs))
    elif status == "failed":
        # Two failure modes: bounce-suppressed (sent_ts set + note) OR dispatch failed
        if dispatch_failed_refs:
            q = q.filter(or_(
                CampaignContact.contact_ref.in_(dispatch_failed_refs),
                CampaignContact.notes.contains("[K3] Skipped"),
            ))
        else:
            q = q.filter(CampaignContact.notes.contains("[K3] Skipped"))

    total = q.count()
    rows = (
        q.order_by(CampaignContact.message_sent_ts.desc().nullslast())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # ── Hard-bounce lookup for all emails in this result set ──
    emails_in_result = [c.email for c in rows if c.email]
    hard_bounced_emails: set[str] = set()
    if emails_in_result:
        bounce_rows = db.query(EmailBounceEvent.email).filter(
            EmailBounceEvent.email.in_(emails_in_result),
            EmailBounceEvent.bounce_class == "hard",
        ).all()
        hard_bounced_emails = {r.email for r in bounce_rows}

    # ── Failure reason from MessageLog (latest entry per contact_ref) ──
    failure_reasons: dict[str, str] = {}
    if dispatch_failed_refs:
        log_details = db.query(MessageLog).filter(
            MessageLog.to_ref.in_(dispatch_failed_refs),
            MessageLog.correlation_ref == campaign_id,
            MessageLog.status == "failed",
        ).order_by(MessageLog.ts.desc()).all()
        for lr in log_details:
            if lr.to_ref and lr.to_ref not in failure_reasons:
                failure_reasons[lr.to_ref] = "SMTP send failed"

    def _classify(c) -> str | None:
        if c.notes and "[K3] Skipped" in c.notes:
            return "Hard bounce — suppressed (address previously rejected)"
        if c.contact_ref in dispatch_failed_refs:
            return failure_reasons.get(c.contact_ref, "SMTP send failed")
        if c.email and c.email in hard_bounced_emails:
            return "Hard bounce detected after delivery"
        return None

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "contacts": [
            {
                "contact_ref": c.contact_ref,
                "name": c.name,
                "email": c.email,
                "alt_email": c.alt_email,
                "msisdn": c.msisdn,
                "contact_type": c.contact_type,
                "sent_at": c.message_sent_ts.isoformat() if c.message_sent_ts else None,
                "response_type": c.response_type,
                "bounced": c.email in hard_bounced_emails if c.email else False,
                "failure_reason": _classify(c),
            }
            for c in rows
        ],
    }


@router.get("/{campaign_id}/report")
def campaign_report(campaign_id: str, db: Session = Depends(get_db)):
    """K3 broadcast report for a campaign."""
    from app.agents.k3_broadcast import K3Broadcast
    agent = K3Broadcast()
    result = agent.execute(
        {"action": "report", "campaign_id": campaign_id},
        requested_by="api",
    )
    if result.status != "SUCCESS":
        raise HTTPException(404, result.error or "Campaign not found")
    return result.output


@router.get("/optouts")
def list_optouts(db: Session = Depends(get_db)):
    """List all contacts currently on the opt-out suppression list."""
    from app.models.enterprise import ContactOptOut
    rows = db.query(ContactOptOut).order_by(ContactOptOut.opted_out_at.desc()).all()
    return [
        {
            "contact_ref": r.contact_ref,
            "name": r.name,
            "email": r.email,
            "msisdn": r.msisdn,
            "opted_out_at": r.opted_out_at.isoformat() if r.opted_out_at else None,
            "campaign_id": r.campaign_id,
        }
        for r in rows
    ]


@router.post("/optouts/{contact_ref}/resubscribe")
def resubscribe(contact_ref: str, db: Session = Depends(get_db)):
    """Remove a contact from the opt-out list (re-subscribe them)."""
    from app.models.enterprise import ContactOptOut, WaContact
    row = db.get(ContactOptOut, contact_ref)
    if not row:
        raise HTTPException(404, f"'{contact_ref}' is not on the opt-out list")
    db.delete(row)
    # Also re-enable WaContact if one exists
    wa = db.execute(
        select(WaContact).where(WaContact.contact_ref == contact_ref)
    ).scalars().first()
    if wa:
        wa.opt_in_status = "opt_in"
    db.commit()
    logger.info("Resubscribed: %s", contact_ref)
    return {"resubscribed": contact_ref}


@router.delete("/optouts/{contact_ref}")
def delete_optout(contact_ref: str, db: Session = Depends(get_db)):
    """Alias for resubscribe — DELETE /campaigns/optouts/{contact_ref}."""
    return resubscribe(contact_ref, db)


def _unsub_page(contact_ref: str) -> str:
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Unsubscribed — Yexis Electronics</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 480px; margin: 80px auto; text-align: center; color: #333; }}
    h1 {{ color: #1a56db; }}
    p {{ color: #666; line-height: 1.6; }}
  </style>
</head>
<body>
  <h1>You've been unsubscribed</h1>
  <p>We've removed <strong>{contact_ref}</strong> from our campaign list.<br>
  You will no longer receive promotional messages from Yexis Electronics.</p>
  <p style="margin-top:32px; font-size:12px; color:#aaa;">
    If this was a mistake, please contact your Yexis representative to re-subscribe.
  </p>
</body>
</html>
"""


@router.get("/{campaign_id}")
def get_campaign(campaign_id: str, db: Session = Depends(get_db)):
    """Read one campaign and its current audience/send counts."""
    from app.models.enterprise import Campaign, CampaignContact
    from sqlalchemy import func

    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    audience_count = db.query(func.count(CampaignContact.id)).filter(
        CampaignContact.campaign_id == campaign.id
    ).scalar() or 0
    sent_count = db.query(func.count(CampaignContact.id)).filter(
        CampaignContact.campaign_id == campaign.id,
        CampaignContact.message_sent_ts.isnot(None),
    ).scalar() or 0
    return {
        "campaign_id": str(campaign.id),
        "campaign_ref": campaign.campaign_ref,
        "name": campaign.name,
        "campaign_type": campaign.campaign_type,
        "entity": campaign.entity,
        "status": campaign.status,
        "start_date": str(campaign.start_date) if campaign.start_date else None,
        "end_date": str(campaign.end_date) if campaign.end_date else None,
        "budget": campaign.budget,
        "default_channel": campaign.default_channel,
        "message_template_id": campaign.message_template_id,
        "target_skus": (campaign.target_skus_json or {}).get("skus", []),
        "created_ts": campaign.created_ts.isoformat() if campaign.created_ts else None,
        "audience_count": audience_count,
        "sent_count": sent_count,
        "scheduled_at": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
        "kpi": campaign.kpi_json or {},
    }
