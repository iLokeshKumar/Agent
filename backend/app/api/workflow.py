"""Workflow endpoints — start runs and inspect their state.

The workflow is a first-class object: a client starts one by name and
reads its state as it moves, instead of POSTing each step by hand.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.workflow import (
    WorkflowRun,
    WorkflowStep,
    WorkflowStepRun,
)
from orchestrator.queue import task_queue


router = APIRouter(tags=["workflows"])


class StartWorkflowRequest(BaseModel):
    payload: dict = {}
    requested_by: str | None = None


def _step_run_view(step_run: WorkflowStepRun) -> dict:
    return {
        "position": step_run.position,
        "step_key": step_run.step_key,
        "task_type": step_run.task_type,
        "task_id": str(step_run.task_id) if step_run.task_id else None,
        "status": step_run.status,
        "note": step_run.note,
        "started_at": step_run.started_at,
        "completed_at": step_run.completed_at,
    }


@router.get("/workflows")
def list_workflows(db: Session = Depends(get_db)):
    """The definitions — what can be started."""
    steps = db.execute(
        select(WorkflowStep).order_by(
            WorkflowStep.workflow_code.asc(),
            WorkflowStep.position.asc(),
        )
    ).scalars().all()

    workflows: dict[str, list] = {}

    for step in steps:
        workflows.setdefault(step.workflow_code, []).append({
            "position": step.position,
            "step_key": step.step_key,
            "task_type": step.task_type,
            "description": step.description,
        })

    return [
        {"workflow": code, "steps": steps_}
        for code, steps_ in workflows.items()
    ]


@router.post("/workflows/{workflow_code}/start")
def start_workflow(
    workflow_code: str,
    request: StartWorkflowRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    from orchestrator.workflow_engine import start_run

    actor = get_current_user(http_request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in to start workflows.")
    if not actor.can("tasks", "edit"):
        raise HTTPException(status_code=403, detail="You do not have permission to start workflows.")
    if request.requested_by and request.requested_by != actor.username:
        raise HTTPException(status_code=403, detail="requested_by must match the signed-in user.")
    requested_by = actor.username

    try:
        run = start_run(
            db, workflow_code, request.payload, requested_by
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    db.commit()

    return {
        "workflow_run_id": str(run.id),
        "workflow": run.workflow_code,
        "correlation_id": str(run.correlation_id),
        "status": run.status,
        "requested_by": requested_by,
    }


@router.get("/workflow-runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db)):
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 100",
        )

    runs = db.execute(
        select(WorkflowRun)
        .order_by(WorkflowRun.created_at.desc())
        .limit(limit)
    ).scalars().all()

    return [
        {
            "workflow_run_id": str(run.id),
            "workflow": run.workflow_code,
            "status": run.status,
            "current_position": run.current_position,
            "correlation_id": str(run.correlation_id),
            "created_at": run.created_at,
            "error": run.error,
        }
        for run in runs
    ]


@router.get("/workflow-runs/{run_id}")
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    run = db.get(WorkflowRun, run_id)

    if run is None:
        raise HTTPException(
            status_code=404,
            detail="Workflow run not found",
        )

    step_runs = db.execute(
        select(WorkflowStepRun)
        .where(WorkflowStepRun.workflow_run_id == run.id)
        .order_by(WorkflowStepRun.position.asc())
    ).scalars().all()

    return {
        "workflow_run_id": str(run.id),
        "workflow": run.workflow_code,
        "status": run.status,
        "current_position": run.current_position,
        "correlation_id": str(run.correlation_id),
        "requested_by": run.requested_by,
        "context": run.context,
        "error": run.error,
        "created_at": run.created_at,
        "steps": [_step_view(s) for s in step_runs],
    }


def _step_view(step_run: WorkflowStepRun) -> dict:
    return _step_run_view(step_run)
