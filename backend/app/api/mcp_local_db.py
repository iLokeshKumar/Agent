"""Local-DB MCP tools — typed access to the governance store.

The docs' rule for agent-facing database access: **typed operations
only, no free-form SQL**. These routes are that rule made physical —
the only way an external session (Cowork, headless worker) touches the
local store is through one of these endpoints:

    ledger.append / ledger.query     audit trail (append-only)
    approvals.list / decide          human gate
    watermark.get / watermark.set    incremental-pull cursors

Deletes do not exist here. The ledger accepts INSERT and SELECT and
nothing else; a correction is a new row, never an edit.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.action_ledger import ActionLedger
from app.models.sync_watermark import SyncWatermark
from app.repository.approval_repository import ApprovalRepository


router = APIRouter(prefix="/mcp/db", tags=["mcp-local-db"])


# ── Ledger: append + query, nothing else ───────────────────────────


class LedgerAppendRequest(BaseModel):
    action: str
    tool_name: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    status: str = "SUCCESS"
    error_code: str | None = None
    error_message: str | None = None
    task_id: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None
    input_ref: str | None = None
    output_ref: str | None = None


@router.post("/ledger/append")
def ledger_append(request: LedgerAppendRequest, db: Session = Depends(get_db)):
    entry = ActionLedger(
        action=request.action,
        tool_name=request.tool_name,
        entity_type=request.entity_type,
        entity_id=request.entity_id,
        status=request.status,
        error_code=request.error_code,
        error_message=request.error_message,
        task_id=request.task_id,
        correlation_id=request.correlation_id,
        input_ref=request.input_ref,
        output_ref=request.output_ref,
    )
    db.add(entry)
    db.commit()
    return {"id": str(entry.id), "appended": True}


@router.get("/ledger")
def ledger_query(
    correlation_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = (
        select(ActionLedger)
        .order_by(ActionLedger.created_at.desc())
        .limit(limit)
    )
    if correlation_id is not None:
        query = query.where(
            ActionLedger.correlation_id == correlation_id
        )
    if task_id is not None:
        query = query.where(ActionLedger.task_id == task_id)

    rows = list(db.execute(query).scalars())
    return [
        {
            "id": str(r.id),
            "action": r.action,
            "tool_name": r.tool_name,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "status": r.status,
            "error_code": r.error_code,
            "created_at": r.created_at,
            "correlation_id": (
                str(r.correlation_id) if r.correlation_id else None
            ),
        }
        for r in rows
    ]


# ── Approvals: list + decide ────────────────────────────────────────


class DecideRequest(BaseModel):
    decided_by: str
    reason: str | None = None


@router.get("/approvals")
def approvals_list(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    rows = ApprovalRepository(db).list_pending()[:limit]
    return [
        {
            "id": str(a.id),
            "approval_type": a.approval_type,
            "task_id": str(a.task_id) if a.task_id else None,
            "status": a.status,
            "requested_by": a.requested_by,
            "payload": a.payload,
        }
        for a in rows
    ]


@router.post("/approvals/{approval_id}/approve")
def approval_approve(
    approval_id: uuid.UUID,
    request: DecideRequest,
    db: Session = Depends(get_db),
):
    repo = ApprovalRepository(db)
    approval = repo.get(approval_id)
    if approval is None:
        raise HTTPException(404, "approval not found")
    if approval.status != "PENDING":
        raise HTTPException(409, f"approval already {approval.status}")
    updated = repo.approve(
        approval, approved_by=request.decided_by,
        reason=request.reason,
    )
    db.commit()
    return {"id": str(updated.id), "status": updated.status}


@router.post("/approvals/{approval_id}/reject")
def approval_reject(
    approval_id: uuid.UUID,
    request: DecideRequest,
    db: Session = Depends(get_db),
):
    repo = ApprovalRepository(db)
    approval = repo.get(approval_id)
    if approval is None:
        raise HTTPException(404, "approval not found")
    if approval.status != "PENDING":
        raise HTTPException(409, f"approval already {approval.status}")
    updated = repo.reject(
        approval, approved_by=request.decided_by,
        reason=request.reason,
    )
    db.commit()
    return {"id": str(updated.id), "status": updated.status}


# ── Watermarks: get/set cursors ─────────────────────────────────────


class WatermarkSetRequest(BaseModel):
    cursor: str | None = None
    extra: dict = {}


def _watermark_view(row: SyncWatermark) -> dict:
    return {
        "lane": row.lane,
        "source": row.source,
        "cursor": row.cursor,
        "extra": row.extra,
        "updated_at": row.updated_at,
    }


@router.get("/watermark/{lane}/{source}")
def watermark_get(lane: str, source: str, db: Session = Depends(get_db)):
    row = _get_watermark(db, lane, source)
    if row is None:
        return {"lane": lane, "source": source, "cursor": None}
    return _watermark_view(row)


@router.post("/watermark/{lane}/{source}")
def watermark_set(
    lane: str,
    source: str,
    request: WatermarkSetRequest,
    db: Session = Depends(get_db),
):
    row = _get_watermark(db, lane, source)
    if row is None:
        row = SyncWatermark(id=uuid.uuid4(), lane=lane, source=source)
        db.add(row)
    row.cursor = request.cursor
    if request.extra:
        row.extra = dict(request.extra)  # new object — JSONB change tracking
    db.commit()
    return _watermark_view(row)


def _get_watermark(db: Session, lane: str, source: str):
    return db.execute(
        select(SyncWatermark).where(
            SyncWatermark.lane == lane,
            SyncWatermark.source == source,
        )
    ).scalars().first()
