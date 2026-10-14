"""Tally Gateway MCP tools — exposed over HTTP for Cowork sessions.

The tool set mirrors the spec exactly:

    stage_voucher      agents stage rendered voucher lines (STAGED)
    list_staged        accountant console: what awaits approval
    approve / reject   the human gate
    post_approved      the ONLY write path — refuses unapproved rows
    fetch_trial_balance   read-back from Tally

Every route is a thin wrapper; invariants live in
``app.integrations.tally_gateway`` and cannot be bypassed from here.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.integrations.tally_gateway import (
    TallyGateway,
    TallyGatewayError,
)
from app.models.tally_staging import TallyStagingVoucher


router = APIRouter(prefix="/mcp/tally", tags=["mcp-tally"])


class StageRequest(BaseModel):
    entity: str = "YEXIS-EL"
    books_txn_id: str
    entries: list[dict]
    company: str = "Default Company"


def _row_view(row: TallyStagingVoucher) -> dict:
    return {
        "id": str(row.id),
        "entity": row.entity,
        "books_txn_id": row.books_txn_id,
        "line_no": row.line_no,
        "voucher_type": row.voucher_type,
        "voucher_date": (
            row.voucher_date.isoformat() if row.voucher_date else None
        ),
        "tally_account": row.tally_account,
        "debit": row.debit,
        "credit": row.credit,
        "status": row.status,
        "approved_by": row.approved_by,
        "tally_voucher_no": row.tally_voucher_no,
        "error_message": row.error_message,
    }


@router.post("/stage_voucher")
def stage_voucher(request: StageRequest, db: Session = Depends(get_db)):
    try:
        rows = TallyGateway(db).stage_entries(
            entity=request.entity,
            books_txn_id=request.books_txn_id,
            entries=request.entries,
            company=request.company,
        )
    except TallyGatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    db.commit()
    return {"staged": len(rows), "rows": [_row_view(r) for r in rows]}


@router.get("/list_staged")
def list_staged(
    entity: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    rows = TallyGateway(db).list_staged(entity=entity, limit=limit)
    return [_row_view(r) for r in rows]


class DecideRequest(BaseModel):
    staging_ids: list[str]
    approved_by: str


@router.post("/approve")
def approve(request: DecideRequest, db: Session = Depends(get_db)):
    try:
        result = TallyGateway(db).approve(
            [uuid.UUID(i) for i in request.staging_ids],
            request.approved_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"bad id: {exc}")
    except TallyGatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    db.commit()
    return result


class RejectRequest(BaseModel):
    staging_ids: list[str]
    reason: str


@router.post("/reject")
def reject(request: RejectRequest, db: Session = Depends(get_db)):
    try:
        count = TallyGateway(db).reject(
            [uuid.UUID(i) for i in request.staging_ids],
            request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"bad id: {exc}")
    except TallyGatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    db.commit()
    return {"rejected": count}


class PostRequest(BaseModel):
    entity: str = "YEXIS-EL"
    books_txn_ids: list[str] | None = None


@router.post("/post_approved")
def post_approved(request: PostRequest, db: Session = Depends(get_db)):
    try:
        result = TallyGateway(db).post_approved(
            entity=request.entity,
            books_txn_ids=request.books_txn_ids,
        )
    except TallyGatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    db.commit()
    return result


@router.get("/fetch_trial_balance")
def fetch_trial_balance(entity: str, db: Session = Depends(get_db)):
    try:
        rows = TallyGateway(db).trial_balance(entity)
    except TallyGatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"entity": entity, "lines": rows}
