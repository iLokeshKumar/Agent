"""Template Registry API — create, list, preview, and approve message templates.

GET    /templates                    list all (filter: ?channel=email&status=internal)
POST   /templates                    create a new template
GET    /templates/{id}               get one template
PUT    /templates/{id}               update body / purpose / language / variables
DELETE /templates/{id}               delete a template
POST   /templates/{id}/approve       set approval_status (pending|internal|meta|dlt)
POST   /templates/{id}/preview       render with caller-supplied context → rendered_body
"""

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/templates", tags=["templates"])

VALID_CHANNELS = {"email", "wa", "telegram", "push", "voice", "linkedin"}
VALID_STATUSES = {"pending", "internal", "meta", "dlt"}

# Template attachments live alongside the mail-draft attachments, under a
# dedicated templates/ subtree so a template_id can never collide with a
# draft UUID.
ATTACHMENT_DIR = Path(__file__).resolve().parents[2] / "attachments" / "templates"

def _row(t) -> dict:
    return {
        "template_id": t.template_id,
        "channel": t.channel,
        "purpose": t.purpose,
        "language": t.language,
        "subject": getattr(t, "subject", None),
        "reply_to": getattr(t, "reply_to", None),
        "body": t.body,
        "variables_json": t.variables_json,
        "approval_status": t.approval_status,
        "approved_by": t.approved_by,
        "version": t.version,
        "segment_overrides_json": t.segment_overrides_json,
        "attachments": getattr(t, "attachments_json", None) or [],
    }


def _jinja_render(body: str, ctx: dict) -> str:
    from jinja2 import Environment, Undefined

    class _Silent(Undefined):
        def __str__(self): return ""
        def __bool__(self): return False
        def __iter__(self): return iter([])
        def __len__(self): return 0
        def __int__(self): return 0
        def __float__(self): return 0.0
        def __round__(self, *a): return 0
        def __getattr__(self, k): return _Silent()
        def __getitem__(self, k): return _Silent()

    env = Environment(undefined=_Silent, autoescape=False)
    clean = {k: ("" if v is None else v) for k, v in ctx.items()}
    return env.from_string(body).render(**clean)


# ── list ──────────────────────────────────────────────────────────────

@router.get("")
def list_templates(
    channel: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    from app.models.enterprise import TemplateRegistry
    q = db.query(TemplateRegistry)
    if channel:
        q = q.filter(TemplateRegistry.channel == channel)
    if status:
        q = q.filter(TemplateRegistry.approval_status == status)
    return [_row(t) for t in q.order_by(TemplateRegistry.template_id).all()]


# ── create ────────────────────────────────────────────────────────────

@router.post("", status_code=201)
def create_template(body: dict, db: Session = Depends(get_db)):
    from app.models.enterprise import TemplateRegistry
    tid = (body.get("template_id") or "").strip()
    if not tid:
        raise HTTPException(400, "template_id is required")
    if db.get(TemplateRegistry, tid):
        raise HTTPException(409, f"Template '{tid}' already exists")
    channel = body.get("channel", "email")
    if channel not in VALID_CHANNELS:
        raise HTTPException(400, f"channel must be one of {sorted(VALID_CHANNELS)}")
    t = TemplateRegistry(
        template_id=tid,
        channel=channel,
        purpose=body.get("purpose") or "",
        language=body.get("language") or "en",
        subject=body.get("subject") or None,
        reply_to=body.get("reply_to") or None,
        body=body.get("body") or "",
        variables_json=body.get("variables_json"),
        approval_status=body.get("approval_status") or "pending",
        segment_overrides_json=body.get("segment_overrides_json"),
    )
    db.add(t)
    db.commit()
    logger.info("Template created: %s (%s)", tid, channel)
    return _row(t)


# ── get one ───────────────────────────────────────────────────────────

@router.get("/{template_id}")
def get_template(template_id: str, db: Session = Depends(get_db)):
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")
    return _row(t)


# ── update ────────────────────────────────────────────────────────────

@router.put("/{template_id}")
def update_template(template_id: str, body: dict, db: Session = Depends(get_db)):
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")
    for field in ("channel", "purpose", "language", "subject", "reply_to", "body", "variables_json", "segment_overrides_json"):
        if field in body:
            setattr(t, field, body[field])
    t.version = (t.version or 1) + 1
    db.commit()
    return _row(t)


# ── delete ────────────────────────────────────────────────────────────

@router.delete("/{template_id}")
def delete_template(template_id: str, db: Session = Depends(get_db)):
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")
    db.delete(t)
    db.commit()
    return {"deleted": template_id}


# ── approve ───────────────────────────────────────────────────────────

@router.post("/{template_id}/approve")
def approve_template(template_id: str, body: dict, db: Session = Depends(get_db)):
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")
    status = body.get("approval_status", "internal")
    if status not in VALID_STATUSES:
        raise HTTPException(400, f"approval_status must be one of {sorted(VALID_STATUSES)}")
    t.approval_status = status
    t.approved_by = body.get("approved_by") or "ui"
    db.commit()
    return _row(t)


# ── preview ───────────────────────────────────────────────────────────

@router.post("/{template_id}/preview")
def preview_template(template_id: str, body: dict | None = None, db: Session = Depends(get_db)):
    """Render a template with caller-supplied context, without invented contact data.

    Body (optional): { "context": {"dealer_name": "...", ...} }
    Returns: { template_id, channel, rendered_body, context }
    """
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")

    ctx = (body or {}).get("context") or {}
    try:
        rendered = _jinja_render(t.body, ctx)
    except Exception as exc:
        raise HTTPException(422, f"Template render error: {exc}")

    rendered_subject = None
    if getattr(t, "subject", None):
        try:
            rendered_subject = _jinja_render(t.subject, ctx)
        except Exception:
            rendered_subject = t.subject

    return {
        "template_id": template_id,
        "channel": t.channel,
        "purpose": t.purpose,
        "subject": t.subject,
        "rendered_subject": rendered_subject,
        "reply_to": getattr(t, "reply_to", None),
        "rendered_body": rendered,
        "context": ctx,
        "attachments": getattr(t, "attachments_json", None) or [],
    }


# ── validate (content-correctness check) ───────────────────────────────

@router.post("/{template_id}/validate")
def validate_template(template_id: str, body: dict | None = None, db: Session = Depends(get_db)):
    """Run the content-correctness checks on a template before it is sent.

    Structural lint (always): Jinja syntax, unknown/typo'd merge tokens,
    leaked tokens, blank fields, missing attachments, empty body/subject,
    suspicious links. These are what K3's send gate blocks on.

    AI proofread (optional, body.proofread=true): spelling/grammar via the
    configured model provider. Advisory only — never blocks a send.

    Body (all optional):
      { "proofread": true,
        "campaign_id": "...",   # render against this campaign's data
        "contact_ref": "..." }  # and this specific contact (else the first)

    Returns: { template_id, lint: {...}, proofread: {...}|null }
    """
    from app.models.enterprise import Campaign, CampaignContact, TemplateRegistry
    from app.services.campaign_lint import lint_template

    body = body or {}
    t = db.get(TemplateRegistry, template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")

    # Pick a sample contact + campaign so the body renders against real data.
    sample_contact = campaign = None
    campaign_id = body.get("campaign_id")
    if campaign_id:
        campaign = db.get(Campaign, campaign_id)
        if campaign is not None:
            q = db.query(CampaignContact).filter(CampaignContact.campaign_id == campaign.id)
            ref = body.get("contact_ref")
            if ref:
                q = q.filter(CampaignContact.contact_ref == ref)
            sample_contact = q.first()

    lint = lint_template(t, sample_contact=sample_contact, campaign=campaign, db=db)

    proofread = None
    if body.get("proofread"):
        from app.services.campaign_proofread import proofread as _proofread
        # Prefer the rendered body/subject (what the customer actually sees).
        rendered_body = (lint.get("summary") or {}).get("rendered_body") or t.body or ""
        rendered_subject = (lint.get("summary") or {}).get("rendered_subject") or (t.subject or "")
        proofread = _proofread(rendered_subject, rendered_body)

    return {"template_id": template_id, "lint": lint, "proofread": proofread}


# ── attachments ────────────────────────────────────────────────────────
#
# Attachments are shared across every recipient of the template (they are
# not personalized per contact). The bytes live on disk; the DB row holds
# only metadata. K3's _send_smtp_email attaches them on every send.

@router.post("/{template_id}/attachments")
async def upload_template_attachment(
    template_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Attach a file (image, PDF, slides, ...) to a template."""
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")

    dest_dir = ATTACHMENT_DIR / template_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "attachment").name
    dest = dest_dir / safe_name
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{dest.stem}_{counter}{dest.suffix}"
        counter += 1

    content = await file.read()
    dest.write_bytes(content)

    mime_type = file.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    meta = {"filename": dest.name, "path": str(dest), "size": len(content), "mime_type": mime_type}

    current = list(t.attachments_json or [])
    current.append(meta)
    t.attachments_json = current
    db.commit()
    return {"attached": meta, "total": len(t.attachments_json)}


@router.delete("/{template_id}/attachments/{filename}")
def delete_template_attachment(template_id: str, filename: str, db: Session = Depends(get_db)):
    """Remove one attachment (by filename) from a template."""
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if not t:
        raise HTTPException(404, f"Template '{template_id}' not found")

    updated = [a for a in (t.attachments_json or []) if a.get("filename") != filename]
    removed = len(t.attachments_json or []) - len(updated)
    if removed:
        dest = ATTACHMENT_DIR / template_id / filename
        if dest.exists():
            dest.unlink()
        t.attachments_json = updated
        db.commit()
    return {"removed": removed, "total": len(updated)}
