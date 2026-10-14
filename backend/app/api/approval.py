import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.repository.approval_repository import ApprovalRepository
from app.repository.task_repository import TaskRepository
from app.services.card_renderer import verify_callback_token
from orchestrator.queue import task_queue


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/approvals",
    tags=["approvals"],
)


def _require_approval_permission(request: Request, action: str):
    from app.core.auth import get_current_user

    actor = get_current_user(request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in to access approvals.")
    if not actor.can("approvals", action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} approvals.",
        )
    return actor


class ApproveRequest(BaseModel):
    approved_by: str
    reason: str | None = None


class RejectRequest(BaseModel):
    approved_by: str
    reason: str | None = None


@router.get("")
def list_approvals(
    http_request: Request,
    status: str | None = None,
    approval_type: str | None = None,
    db: Session = Depends(get_db),
):
    _require_approval_permission(http_request, "view")
    repository = ApprovalRepository(db)

    if status == "PENDING":
        approvals = repository.list_pending(
            approval_type=approval_type,
        )
    else:
        from sqlalchemy import select
        from app.models.approval import Approval

        statement = select(Approval).order_by(
            Approval.requested_at.desc()
        )

        if status:
            statement = statement.where(
                Approval.status == status
            )
        if approval_type:
            statement = statement.where(
                Approval.approval_type == approval_type
            )

        approvals = list(
            db.execute(statement).scalars().all()
        )

    return [
        {
            "id": str(a.id),
            "task_id": str(a.task_id) if a.task_id else None,
            "approval_type": a.approval_type,
            "status": a.status,
            "requested_by": a.requested_by,
            "approved_by": a.approved_by,
            "reason": a.reason,
            "payload": a.payload,
            "requested_at": a.requested_at,
            "decided_at": a.decided_at,
        }
        for a in approvals
    ]


@router.get("/{approval_id}")
def get_approval(
    approval_id: uuid.UUID,
    http_request: Request,
    db: Session = Depends(get_db),
):
    _require_approval_permission(http_request, "view")
    repository = ApprovalRepository(db)

    approval = repository.get(approval_id)

    if approval is None:
        raise HTTPException(
            status_code=404,
            detail="Approval not found",
        )

    return {
        "id": str(approval.id),
        "task_id": (
            str(approval.task_id)
            if approval.task_id else None
        ),
        "approval_type": approval.approval_type,
        "status": approval.status,
        "requested_by": approval.requested_by,
        "approved_by": approval.approved_by,
        "reason": approval.reason,
        "payload": approval.payload,
        "requested_at": approval.requested_at,
        "decided_at": approval.decided_at,
    }


@router.post("/{approval_id}/approve")
def approve_action(
    approval_id: uuid.UUID,
    request: ApproveRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    actor = _require_approval_permission(http_request, "edit")
    repository = ApprovalRepository(db)

    approval = repository.get(approval_id)

    if approval is None:
        raise HTTPException(
            status_code=404,
            detail="Approval not found",
        )

    if approval.status != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Approval is already {approval.status}"
            ),
        )

    repository.approve(
        approval,
        approved_by=actor.username,
        reason=request.reason,
    )

    # --- Re-queue the linked task ---
    requeued = False
    if approval.task_id:
        task_repo = TaskRepository(db)
        task = task_repo.get(approval.task_id)

        if task and task.status == "AWAITING_APPROVAL":
            task_repo.requeue_task(task)

            task_queue.put(
                task_id=task.id,
                priority=task.priority,
            )

            requeued = True

            logger.info(
                "Task %s re-queued after approval %s",
                task.id,
                approval.id,
            )

    return {
        "id": str(approval.id),
        "status": "APPROVED",
        "approved_by": actor.username,
        "task_id": str(approval.task_id) if approval.task_id else None,
        "task_requeued": requeued,
    }


@router.post("/{approval_id}/reject")
def reject_action(
    approval_id: uuid.UUID,
    request: RejectRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    actor = _require_approval_permission(http_request, "edit")
    repository = ApprovalRepository(db)

    approval = repository.get(approval_id)

    if approval is None:
        raise HTTPException(
            status_code=404,
            detail="Approval not found",
        )

    if approval.status != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Approval is already {approval.status}"
            ),
        )

    repository.reject(
        approval,
        approved_by=actor.username,
        reason=request.reason,
    )

    # --- Close the linked task as a normal business rejection ---
    if approval.task_id:
        task_repo = TaskRepository(db)
        task = task_repo.get(approval.task_id)

        if task and task.status == "AWAITING_APPROVAL":
            reason_text = (
                request.reason
                or f"Approval rejected by {actor.username}"
            )
            task_repo.reject_task(task, reason_text)

            logger.info(
            "Task %s rejected after rejection of approval %s",
                task.id,
                approval.id,
            )

    return {
        "id": str(approval.id),
        "status": "REJECTED",
        "approved_by": actor.username,
        "task_id": str(approval.task_id) if approval.task_id else None,
    }


# ── Card delivery ─────────────────────────────────────────────────────


class DeliverRequest(BaseModel):
    channel: str          # "email" | "whatsapp"
    recipient: str        # email address or phone number (E.164 without +)
    sla_hours: int | None = None


@router.post("/{approval_id}/deliver")
def deliver_card(
    approval_id: uuid.UUID,
    request: DeliverRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    """Send an approval card to the approver's inbox or WhatsApp.

    Records delivery on the approval row. Idempotent: re-delivering
    an already-delivered approval re-sends the card and updates the SLA
    deadline, which is the correct behaviour for reminders.
    """
    from app.services.card_delivery import deliver_card as _deliver
    _require_approval_permission(http_request, "edit")

    if request.channel not in ("email", "whatsapp"):
        raise HTTPException(400, "channel must be 'email' or 'whatsapp'")

    approval = ApprovalRepository(db).get(approval_id)
    if approval is None:
        raise HTTPException(404, "Approval not found")

    if approval.status != "PENDING":
        raise HTTPException(400, f"Approval is already {approval.status}")

    try:
        _deliver(
            approval=approval,
            channel=request.channel,
            recipient=request.recipient,
            db=db,
            sla_hours=request.sla_hours,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    return {
        "id": str(approval.id),
        "delivered_via": request.channel,
        "recipient": request.recipient,
        "sla_deadline": approval.sla_deadline.isoformat()
        if approval.sla_deadline
        else None,
    }


# ── Email callback (one-click approve / reject) ───────────────────────


_CALLBACK_HTML = """\
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>body{{font-family:Arial,sans-serif;text-align:center;padding:40px;}}
.icon{{font-size:48px;}} .msg{{font-size:20px;margin-top:16px;}} .sub{{color:#888;margin-top:8px;}}</style>
</head><body>
<div class="icon">{icon}</div>
<div class="msg">{message}</div>
<div class="sub">{sub}</div>
</body></html>"""


@router.get("/callback", response_class=HTMLResponse, include_in_schema=False)
def approval_email_callback(
    token: str = Query(...),
    by: str = Query(default="email_link"),
    db: Session = Depends(get_db),
):
    """One-click approve/reject endpoint embedded in approval emails.

    The ``token`` is an HMAC-signed string produced by card_renderer so
    no authentication session is needed. The response is a minimal HTML
    page the approver sees after clicking.
    """
    parsed = verify_callback_token(token)
    if parsed is None:
        return HTMLResponse(
            _CALLBACK_HTML.format(
                icon="❌",
                message="Invalid or expired link.",
                sub="Please ask for a new approval card.",
            ),
            status_code=400,
        )

    approval_id, action = parsed
    repo = ApprovalRepository(db)
    approval = repo.get(approval_id)

    if approval is None:
        return HTMLResponse(
            _CALLBACK_HTML.format(
                icon="❌",
                message="Approval not found.",
                sub="It may have been deleted or the ID is incorrect.",
            ),
            status_code=404,
        )

    if approval.status != "PENDING":
        return HTMLResponse(
            _CALLBACK_HTML.format(
                icon="ℹ️",
                message=f"Already {approval.status.lower()}.",
                sub=f"This approval was decided by {approval.approved_by or 'someone else'}.",
            ),
            status_code=200,
        )

    approver_label = by if by != "email_link" else "email link"

    if action == "approve":
        repo.approve(approval, approved_by=approver_label)
        _requeue_task_if_needed(approval, db)
        return HTMLResponse(
            _CALLBACK_HTML.format(
                icon="✅",
                message="Approved successfully.",
                sub=f"Approval #{str(approval.id)[:8]} has been recorded.",
            )
        )
    else:
        repo.reject(approval, approved_by=approver_label)
        _close_task_if_needed(approval, db, reason="Rejected via email link")
        return HTMLResponse(
            _CALLBACK_HTML.format(
                icon="🚫",
                message="Rejected successfully.",
                sub=f"Approval #{str(approval.id)[:8]} has been recorded.",
            )
        )


def _requeue_task_if_needed(approval, db: Session) -> None:
    if not approval.task_id:
        return
    task = TaskRepository(db).get(approval.task_id)
    if task and task.status == "AWAITING_APPROVAL":
        TaskRepository(db).requeue_task(task)
        task_queue.put(task_id=task.id, priority=task.priority)
        logger.info("Task %s re-queued after email approval", task.id)


def _close_task_if_needed(approval, db: Session, reason: str) -> None:
    if not approval.task_id:
        return
    task = TaskRepository(db).get(approval.task_id)
    if task and task.status == "AWAITING_APPROVAL":
        TaskRepository(db).reject_task(task, reason)
        logger.info("Task %s closed after email rejection", task.id)


# ── SLA check (called by scheduler) ──────────────────────────────────


@router.post("/sla-check", include_in_schema=False)
def run_sla_check(
    escalation_email: str | None = Query(default=None),
    escalation_phone: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Escalate any pending approvals past their SLA deadline.

    Intended to be called by the gateway scheduler (e.g. every hour).
    Falls back to settings.digest_recipient_email / _wa when query
    params are not provided.
    """
    from app.core.config import settings
    from app.services.card_delivery import check_and_escalate_sla

    email = escalation_email or settings.digest_recipient_email or None
    phone = escalation_phone or settings.digest_recipient_wa or None
    count = check_and_escalate_sla(db, escalation_email=email, escalation_phone=phone)
    return {"escalated": count}
