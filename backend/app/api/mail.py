"""Manual POP3 intake and internal, approval-only SMTP drafts."""

import mimetypes
import poplib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.approval import Approval
from app.models.enterprise import EmailBounceEvent, EmailMessage
from app.repository.approval_repository import ApprovalRepository
from app.services.mailbox import configuration_status, poll_pop3, send_approved_draft

router = APIRouter(prefix="/mail", tags=["mail"])

ATTACHMENT_DIR = Path(__file__).resolve().parents[2] / "attachments"


def _view(row: EmailMessage) -> dict:
    return {
        "id": str(row.id), "direction": row.direction, "status": row.status,
        "sender": row.sender, "recipient": row.recipient, "subject": row.subject,
        "body": row.body,
        "attachments": row.attachments or [],
        "correlation_id": str(row.correlation_id) if row.correlation_id else None,
        "approval_id": str(row.approval_id) if row.approval_id else None,
        "created_at": row.created_at, "sent_at": row.sent_at, "error": row.error,
    }


@router.get("/health")
def health():
    return configuration_status()


@router.get("/deliverability")
def deliverability(domain: str | None = None, selector: str | None = None):
    """Check SPF / DKIM / DMARC for the sending domain via live DNS lookups.

    These three records are the biggest factor in inbox-vs-spam for bulk mail.
    Run this before a campaign to confirm the domain is authenticated.

    Query params (optional):
      domain   — domain to check (default: the domain of SMTP_FROM)
      selector — a DKIM selector to try first (if you use a custom one)

    Returns per-record status (pass/warn/fail), the record found, a plain
    explanation, and an overall verdict.
    """
    from app.services.deliverability import check_deliverability
    return check_deliverability(domain=domain, extra_selectors=selector)


@router.post("/poll")
def poll(limit: int = 25, db: Session = Depends(get_db)):
    try:
        rows = poll_pop3(db, max(1, min(limit, 100)))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, poplib.error_proto) as exc:
        # OSError = connect-level failure; poplib.error_proto = the server
        # dropping the session mid-command (e.g. '-ERR EOF' during UIDL when a
        # single-session mailbox is hit by overlapping pollers). Both are
        # transient POP3 connection problems, not bugs — map to 502, not 500.
        raise HTTPException(
            status_code=502,
            detail=(
                "POP3 connection failed. Check the configured mail host, "
                "port, DNS resolution, and network access."
            ),
        ) from exc
    return {"received": [_view(row) for row in rows], "count": len(rows)}


@router.get("/bounces")
def list_bounces(
    email: str | None = None,
    bounce_class: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """Bounce notifications detected from polled mailbox traffic — the
    'why' behind why an address stopped receiving campaign email.

    bounce_class='hard' means the address is now permanently suppressed
    from future K3 sends. 'soft' means transient (mailbox full,
    greylisting, temporary DNS failure) — recorded but not suppressed.
    """
    query = select(EmailBounceEvent).order_by(EmailBounceEvent.detected_at.desc())
    if email:
        query = query.where(EmailBounceEvent.email == email.strip().lower())
    if bounce_class:
        query = query.where(EmailBounceEvent.bounce_class == bounce_class)
    rows = db.execute(query.limit(max(1, min(limit, 500)))).scalars().all()
    return {
        "count": len(rows),
        "bounces": [
            {
                "id": str(row.id),
                "email": row.email,
                "bounce_class": row.bounce_class,
                "smtp_code": row.smtp_code,
                "diagnostic": row.diagnostic,
                "source_subject": row.source_subject,
                "detected_at": row.detected_at,
                "suppressed": row.bounce_class == "hard",
            }
            for row in rows
        ],
    }


class DraftRequest(BaseModel):
    recipient: EmailStr
    subject: str
    body: str
    requested_by: str = "admin"
    correlation_id: uuid.UUID | None = None


@router.post("/drafts")
def create_draft(request: DraftRequest, db: Session = Depends(get_db)):
    draft = EmailMessage(
        direction="out", status="pending_approval", recipient=str(request.recipient),
        subject=request.subject, body=request.body,
        correlation_id=request.correlation_id or uuid.uuid4(),
    )
    db.add(draft)
    db.flush()
    approval = ApprovalRepository(db).create(
        approval_type="EMAIL_SEND", task_id=None, requested_by=request.requested_by,
        payload={"draft_id": str(draft.id), "recipient": draft.recipient, "subject": draft.subject},
    )
    draft.approval_id = approval.id
    db.commit()
    return _view(draft)


@router.post("/drafts/{draft_id}/attachments")
async def upload_attachment(
    draft_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    draft = db.get(EmailMessage, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status == "sent":
        raise HTTPException(status_code=409, detail="Cannot attach to a sent draft")

    # Save file to disk under attachments/{draft_id}/
    dest_dir = ATTACHMENT_DIR / str(draft_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "attachment").name
    dest = dest_dir / safe_name
    # Avoid collisions
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{dest.stem}_{counter}{dest.suffix}"
        counter += 1

    content = await file.read()
    dest.write_bytes(content)

    mime_type = file.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    meta = {"filename": dest.name, "path": str(dest), "size": len(content), "mime_type": mime_type}

    current = list(draft.attachments or [])
    current.append(meta)
    draft.attachments = current
    db.commit()
    return {"attached": meta, "total": len(draft.attachments)}


@router.delete("/drafts/{draft_id}/attachments/{filename}")
def delete_attachment(draft_id: uuid.UUID, filename: str, db: Session = Depends(get_db)):
    draft = db.get(EmailMessage, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    updated = [a for a in (draft.attachments or []) if a.get("filename") != filename]
    removed = len(draft.attachments or []) - len(updated)
    if removed:
        dest = ATTACHMENT_DIR / str(draft_id) / filename
        if dest.exists():
            dest.unlink()
        draft.attachments = updated
        db.commit()
    return {"removed": removed}


@router.get("/inbox")
def list_inbox(limit: int = 50, db: Session = Depends(get_db)):
    """Return received (inbound) emails, newest first."""
    rows = (
        db.execute(
            select(EmailMessage)
            .where(EmailMessage.direction == "in")
            .order_by(EmailMessage.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [_view(row) for row in rows]


@router.get("/drafts")
def list_drafts(db: Session = Depends(get_db)):
    rows = db.execute(select(EmailMessage).where(EmailMessage.direction == "out").order_by(EmailMessage.created_at.desc())).scalars().all()
    return [_view(row) for row in rows]


@router.post("/drafts/{draft_id}/send")
def send_draft(draft_id: uuid.UUID, db: Session = Depends(get_db)):
    draft = db.get(EmailMessage, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.direction != "out":
        raise HTTPException(status_code=400, detail="Only outbound drafts can be sent")
    if draft.status == "sent":
        return _view(draft)
    if not draft.approval_id:
        raise HTTPException(status_code=409, detail="Draft has no linked approval")

    approval = db.get(Approval, draft.approval_id)
    if approval is None:
        raise HTTPException(status_code=409, detail="Linked approval not found")
    if approval.status != "APPROVED":
        raise HTTPException(status_code=409, detail=f"Approval is {approval.status}")

    try:
        send_approved_draft(db, draft)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return _view(draft)
