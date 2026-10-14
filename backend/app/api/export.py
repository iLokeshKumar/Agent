"""Export endpoints — download Tally XML from completed F3 tasks."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.repository.task_repository import TaskRepository


router = APIRouter(
    prefix="/export",
    tags=["export"],
)


@router.get("/tasks/{task_id}/tally.xml")
def download_tally_xml(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Download Tally XML for a completed F3 task.

    The task must be COMPLETED and have tally_xml in its result.
    """
    repository = TaskRepository(db)
    task = repository.get(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    if task.status != "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail=f"Task is {task.status}, not COMPLETED",
        )

    result = task.result or {}
    tally_xml = result.get("tally_xml")

    if not tally_xml:
        raise HTTPException(
            status_code=404,
            detail=(
                "No Tally XML in task result. "
                "This task may not be an F3 (Books to Tally) task."
            ),
        )

    # Build filename from period
    summary = result.get("summary", {})
    company = summary.get("company", "default")
    period = summary.get("period", "").replace(" to ", "_").replace(" ", "")
    filename = f"tally_{company}_{period}.xml".replace("/", "-")

    return Response(
        content=tally_xml.encode("utf-8"),
        media_type="application/xml",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
        },
    )


@router.get("/tasks/{task_id}/tally-xml-preview")
def preview_tally_xml(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Preview Tally XML as JSON (first 5000 chars)."""
    repository = TaskRepository(db)
    task = repository.get(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    result = task.result or {}
    tally_xml = result.get("tally_xml", "")

    if not tally_xml:
        raise HTTPException(
            status_code=404,
            detail="No Tally XML in task result",
        )

    # Count vouchers
    voucher_count = tally_xml.count("<VOUCHER")

    return {
        "task_id": str(task_id),
        "voucher_count": voucher_count,
        "xml_length": len(tally_xml),
        "preview": tally_xml[:5000],
        "download_url": f"/export/tasks/{task_id}/tally.xml",
    }
