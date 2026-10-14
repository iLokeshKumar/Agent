import logging
import hmac
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.config import settings
from app.core.db import get_db
from app.repository.purchase_request_repository import (
    PurchaseRequestRepository,
)
from app.repository.task_repository import TaskRepository
from app.repository.lane_repository import LaneRepository
from app.repository.vendor_repository import VendorRepository
from app.models.task import Task
from app.models.user import User

from orchestrator.queue import task_queue

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
)


class CreateTaskRequest(BaseModel):
    task_type: str
    source: str | None = None
    priority: int = Field(default=100, ge=0)
    requested_by: str | None = None
    payload: dict = Field(default_factory=dict)

@router.post("")
def create_task(
    request: CreateTaskRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    actor = get_current_user(http_request)
    gateway_authenticated = False
    authorization = http_request.headers.get("Authorization", "")
    service_token = (
        authorization[7:]
        if authorization.startswith("Bearer ")
        else ""
    )
    if (
        actor is None
        and settings.gateway_api_token
        and service_token
        and hmac.compare_digest(service_token, settings.gateway_api_token)
    ):
        service_username = settings.gateway_service_username or settings.admin_username
        service_user = db.query(User).filter(
            User.username == service_username,
            User.enabled.is_(True),
        ).one_or_none()
        if service_user is None:
            raise HTTPException(
                status_code=503,
                detail="The configured gateway service user is missing or disabled.",
            )
        actor = CurrentUser(str(service_user.id), service_user.username, service_user.role)
        gateway_authenticated = True
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in to submit tasks.")
    if not gateway_authenticated and not actor.can("tasks", "edit"):
        raise HTTPException(status_code=403, detail="You do not have permission to submit tasks.")
    if request.requested_by and request.requested_by != actor.username:
        raise HTTPException(status_code=403, detail="requested_by must match the signed-in user.")

    repository = TaskRepository(db)

    requested_by = actor.username

    task = repository.create(
        task_type=request.task_type,
        source=request.source,
        priority=request.priority,
        requested_by=requested_by,
        payload=request.payload,
    )

    if (
        request.task_type == "purchase_request"
        and not _is_dry_run_lane(db, "P1")
    ):
        # The request becomes a document here, at the door — before any
        # policy runs and whatever it later becomes (PO, approval,
        # rejection), the business can query what was asked for.
        _persist_purchase_request(db, task, request.payload)

    task_queue.put(
        task_id=task.id,
        priority=task.priority,
    )

    return {
        "task_id": str(task.id),
        "correlation_id": str(task.correlation_id),
        "status": "QUEUED",
        "requested_by": task.requested_by,
        "priority": task.priority,
    }


def _is_dry_run_lane(db: Session, lane: str) -> bool:
    """Return whether a lane must suppress business-document writes."""
    row = LaneRepository(db).get(lane)
    return bool(row and row.dry_run)

def _persist_purchase_request(
    db: Session,
    task: Task,
    payload: dict,
) -> None:
    """Persist a purchase_request as rows in purchase_requests(+items).

    Runs after the task row is committed, so intake persistence can
    never block or undo task creation: on failure the task still flows
    and only the audit rows are missing. Idempotent by task_id.
    """
    repository = PurchaseRequestRepository(db)

    if repository.get_by_task(task.id) is not None:
        return

    try:
        items = payload.get("items") or []

        total = sum(
            (
                to_decimal(item.get("quantity"))
                * to_decimal(item.get("unit_price"))
                for item in items
            ),
            Decimal("0"),
        )

        vendor_name = payload.get("vendor")
        vendor = (
            VendorRepository(db).get_or_create(vendor_name)
            if vendor_name
            else None
        )

        budget_limit = payload.get("budget_limit")

        purchase_request = repository.create(
            task_id=task.id,
            correlation_id=task.correlation_id,
            total_amount=total,
            requested_by=task.requested_by,
            vendor_id=vendor.id if vendor else None,
            vendor_name=vendor.name if vendor else None,
            budget_limit=(
                to_decimal(budget_limit) if budget_limit else None
            ),
            currency=payload.get("currency", "INR"),
            notes=payload.get("notes"),
            items=items,
            payload=payload,
        )

        db.commit()

        logger.info(
            "Recorded purchase request %s (%s %s, vendor=%s) for task %s",
            purchase_request.id,
            purchase_request.currency,
            purchase_request.total_amount,
            purchase_request.vendor_name,
            task.id,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to persist purchase request for task %s — "
            "task continues without its audit rows",
            task.id,
        )


def to_decimal(value) -> Decimal:
    """Decimal from int/float/str/None; None-safe for payload math."""
    if value is None:
        return Decimal("0")

    return Decimal(str(value))


@router.get("/{task_id}")
def get_task(
    task_id: uuid.UUID,
    http_request: Request,
    db: Session = Depends(get_db),
):
    actor = get_current_user(http_request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in to view tasks.")
    if not actor.can("tasks", "view"):
        raise HTTPException(status_code=403, detail="You do not have permission to view tasks.")
    repository = TaskRepository(db)

    task = repository.get(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    return {
        "task_id": str(task.id),
        "task_type": task.task_type,
        "status": task.status,
        "priority": task.priority,
        "source": task.source,
        "requested_by": task.requested_by,
        "payload": task.payload,
        "result": task.result,
        "error": task.error,
        "correlation_id": str(task.correlation_id),
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "last_error": task.last_error,
    }

@router.post("/{task_id}/requeue")
def requeue_task(
    task_id: uuid.UUID,
    http_request: Request,
    db: Session = Depends(get_db),
):
    """Re-queue an AWAITING_APPROVAL or FAILED task."""
    actor = get_current_user(http_request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in to re-queue tasks.")
    if not actor.can("tasks", "edit"):
        raise HTTPException(status_code=403, detail="You do not have permission to re-queue tasks.")
    repository = TaskRepository(db)

    task = repository.get(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    if task.status not in (
        "AWAITING_APPROVAL", "FAILED", "VALIDATION_ERROR"
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot requeue task in status: {task.status}",
        )

    task.status = "QUEUED"
    task.error = None
    db.commit()

    task_queue.put(
        task_id=task.id,
        priority=task.priority,
    )

    return {
        "task_id": str(task.id),
        "status": "QUEUED",
        "message": "Task re-queued for execution",
    }


@router.get("")
def list_tasks(
    http_request: Request,
    status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    actor = get_current_user(http_request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in to view tasks.")
    if not actor.can("tasks", "view"):
        raise HTTPException(status_code=403, detail="You do not have permission to view tasks.")
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 100",
        )

    repository = TaskRepository(db)

    tasks = repository.list_tasks(
        status=status,
        limit=limit,
    )

    return [
        {
            "task_id": str(task.id),
            "task_type": task.task_type,
            "source": task.source,
            "priority": task.priority,
            "status": task.status,
            "requested_by": task.requested_by,
            "payload": task.payload,
            "correlation_id": str(task.correlation_id),
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
        }
        for task in tasks
    ]
