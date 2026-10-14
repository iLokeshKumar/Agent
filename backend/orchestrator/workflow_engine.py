"""The workflow engine — definitions execute, not code paths.

``chain.py`` hardcodes one sequence: a completed purchase_request
spawns a grn_request, which spawns an invoice_matching, and so on.
This engine replaces that for workflow-managed tasks. The sequence
lives in ``workflow_steps`` rows; the engine reads it and drives a
``workflow_run`` step by step:

    start_run          create a run + PENDING step runs, spawn step 1
    on_task_completed  mark the step done, spawn the next one
    on_task_paused     an approval gate opened — record it on the step
    on_task_failed     fail the run; remaining steps are skipped

Two rules carried over from the chain:

* **By reference.** Step payloads carry document numbers only; each
  agent loads the real document from the database.
* **Nothing is invented.** If no supplier invoice is on file when the
  invoice_matching step starts, that step (and everything after it) is
  SKIPPED. The run status is WAITING_EXTERNAL_INPUT — not COMPLETED —
  because the workflow is suspended waiting for the supplier to deliver.
  Post the invoice when it arrives and resume via a follow-up task.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task import Task
from app.models.workflow import WorkflowRun, WorkflowStep, WorkflowStepRun
from app.repository.invoice_repository import InvoiceRepository
from app.repository.purchase_order_repository import (
    PurchaseOrderRepository,
)
from app.repository.task_repository import TaskRepository


logger = logging.getLogger(__name__)


@dataclass
class _NoWork:
    """Returned by _payload_for when a step has no payload to dispatch.

    Carries the run status to set (WAITING_EXTERNAL_INPUT or
    PARTIALLY_COMPLETED) and the human-readable notes stored on the
    skipped step and its downstream siblings.
    """

    run_status: str
    step_note: str
    skip_note: str


def start_run(
    db: Session,
    workflow_code: str,
    payload: dict,
    requested_by: str | None = None,
) -> WorkflowRun:
    """Create a run for ``workflow_code`` and spawn its first step.

    No commit — the caller owns the transaction, exactly like every
    repository in this codebase.
    """
    steps = _definition(db, workflow_code)

    if not steps:
        raise ValueError(f"Unknown workflow: {workflow_code}")

    now = datetime.now(timezone.utc)

    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_code=workflow_code,
        correlation_id=uuid.uuid4(),
        status="RUNNING",
        current_position=0,
        requested_by=requested_by,
        # The original request travels with the run; document numbers
        # produced along the way are merged in as steps complete.
        context={"request": payload},
    )

    db.add(run)
    db.flush()

    for step in steps:
        db.add(
            WorkflowStepRun(
                id=uuid.uuid4(),
                workflow_run_id=run.id,
                workflow_step_id=step.id,
                position=step.position,
                step_key=step.step_key,
                task_type=step.task_type,
                status="PENDING",
            )
        )

    db.flush()

    _advance_from(db, run, now)

    logger.info(
        "Started workflow %s run %s (%d steps)",
        workflow_code,
        run.id,
        len(steps),
    )

    return run


# ── Executor hooks ───────────────────────────────────────────────────


def on_task_completed(db: Session, task: Task, output: dict) -> bool:
    """Advance the run a completed task belongs to.

    Returns False when the task is not workflow-managed, so the caller
    falls back to the hardcoded chain. Returns True when it advanced —
    or intentionally did not — meaning the chain must not also fire.
    """
    step_run = _step_run_by_task(db, task.id)

    if step_run is None:
        return False

    now = datetime.now(timezone.utc)

    step_run.status = "COMPLETED"
    step_run.completed_at = now
    step_run.output = output or {}

    run = db.get(WorkflowRun, step_run.workflow_run_id)

    _merge_context(run, task.task_type, output or {})

    logger.info(
        "Workflow %s run %s: step %d (%s) completed",
        run.workflow_code,
        run.id,
        step_run.position,
        step_run.step_key,
    )

    _advance_from(db, run, now)

    return True


def on_task_paused(db: Session, task: Task) -> bool:
    """Record that the step's task stopped to await an approval."""
    step_run = _step_run_by_task(db, task.id)

    if step_run is None:
        return False

    step_run.status = "WAITING_APPROVAL"
    step_run.note = "Task paused for approval"

    logger.info(
        "Workflow step %s (%s): waiting for approval",
        step_run.step_key,
        step_run.id,
    )

    return True


def on_task_failed(db: Session, task: Task, error) -> bool:
    """Fail the run; the steps after the failure are skipped."""
    step_run = _step_run_by_task(db, task.id)

    if step_run is None:
        return False

    now = datetime.now(timezone.utc)
    run = db.get(WorkflowRun, step_run.workflow_run_id)

    step_run.status = "FAILED"
    step_run.completed_at = now
    step_run.note = str(error)

    run.status = "FAILED"
    run.error = {"error": str(error), "failed_step": step_run.step_key}

    for pending in db.execute(
        select(WorkflowStepRun).where(
            WorkflowStepRun.workflow_run_id == run.id,
            WorkflowStepRun.status == "PENDING",
        )
    ).scalars():
        pending.status = "SKIPPED"
        pending.note = f"Skipped: {step_run.step_key} failed"
        pending.completed_at = now

    logger.warning(
        "Workflow %s run %s FAILED at step %s: %s",
        run.workflow_code,
        run.id,
        step_run.step_key,
        error,
    )

    return True


# ── Advancement ──────────────────────────────────────────────────────


def _advance_from(db: Session, run: WorkflowRun, now) -> None:
    """Complete engine-managed steps, spawn the next agent step.

    Walks forward from ``current_position``, completing checkpoint
    steps (no task type) as it goes, until it either spawns an agent
    task or reaches the end of the definition.
    """
    while True:
        step_run = db.execute(
            select(WorkflowStepRun)
            .where(
                WorkflowStepRun.workflow_run_id == run.id,
                WorkflowStepRun.position > run.current_position,
            )
            .order_by(WorkflowStepRun.position.asc())
            .limit(1)
        ).scalars().first()

        if step_run is None:
            # Every step accounted for — the run is done.
            run.status = "COMPLETED"
            logger.info("Workflow run %s COMPLETED", run.id)
            return

        run.current_position = step_run.position

        if step_run.task_type is None:
            # Engine-managed checkpoint. The approval gate completes by
            # inspection: reaching it means the previous step settled,
            # so any approval raised during intake was either granted
            # or never needed.
            step_run.status = "COMPLETED"
            step_run.completed_at = now
            step_run.note = "Checkpoint passed"
            continue

        payload = _payload_for(db, run, step_run)

        if isinstance(payload, _NoWork):
            step_run.status = "SKIPPED"
            step_run.note = payload.step_note
            _skip_remaining(
                db, run,
                after=step_run.position,
                at=now,
                note=payload.skip_note,
            )
            run.status = payload.run_status
            logger.info(
                "Workflow run %s %s (step %s skipped — %s)",
                run.id,
                payload.run_status,
                step_run.step_key,
                payload.step_note,
            )
            return

        task = TaskRepository(db).create(
            task_type=step_run.task_type,
            source="workflow",
            priority=100,
            requested_by=run.requested_by,
            payload=payload,
            correlation_id=run.correlation_id,
        )

        step_run.task_id = task.id
        step_run.status = "RUNNING"
        step_run.started_at = now

        from orchestrator.queue import task_queue

        task_queue.put(task_id=task.id, priority=task.priority)

        logger.info(
            "Workflow run %s: spawned %s task %s for step %s",
            run.id,
            step_run.task_type,
            task.id,
            step_run.step_key,
        )

        return


def _skip_remaining(
    db: Session,
    run: WorkflowRun,
    after: int,
    at,
    note: str = "Skipped — an earlier step had no work",
) -> None:
    for pending in db.execute(
        select(WorkflowStepRun).where(
            WorkflowStepRun.workflow_run_id == run.id,
            WorkflowStepRun.position > after,
            WorkflowStepRun.status == "PENDING",
        )
    ).scalars():
        pending.status = "SKIPPED"
        pending.note = note
        pending.completed_at = at


# ── Payload builders: by reference, nothing invented ─────────────────


def _payload_for(
    db: Session,
    run: WorkflowRun,
    step_run: WorkflowStepRun,
) -> "dict | _NoWork":
    """Build the step's task payload from stored documents.

    Returns a dict when there is work to dispatch, or a _NoWork sentinel
    when the step cannot proceed — carrying the run status and notes that
    explain why (waiting for external input vs. partially complete).
    """
    context = run.context or {}
    po_number = context.get("po_number")
    task_type = step_run.task_type

    if task_type == "purchase_request":
        return dict(context.get("request") or {})

    if task_type == "grn_request":
        if not po_number:
            return _NoWork(
                run_status="PARTIALLY_COMPLETED",
                step_note="No purchase order number in run context",
                skip_note="Skipped — no purchase order",
            )

        document = PurchaseOrderRepository(db).get_by_number(po_number)

        if document is None:
            return _NoWork(
                run_status="PARTIALLY_COMPLETED",
                step_note=f"Purchase order {po_number} not found",
                skip_note="Skipped — purchase order not found",
            )

        return {
            "po_number": document.po_number,
            "vendor": document.vendor,
            "items": [
                {
                    "item": line.item,
                    "quantity": float(line.quantity),
                }
                for line in document.items
            ],
        }

    if task_type == "invoice_matching":
        if not po_number:
            return _NoWork(
                run_status="PARTIALLY_COMPLETED",
                step_note="No purchase order number in run context",
                skip_note="Skipped — no purchase order",
            )

        invoice = InvoiceRepository(db).get_unmatched_for_po(po_number)

        if invoice is None:
            return _NoWork(
                run_status="WAITING_EXTERNAL_INPUT",
                step_note=(
                    f"Waiting for supplier invoice against PO {po_number} — "
                    f"post it when it arrives"
                ),
                skip_note=f"Skipped — waiting for supplier invoice (PO {po_number})",
            )

        return {
            "po_number": po_number,
            "invoice_number": invoice.invoice_number,
        }

    if task_type == "books_to_tally":
        if not po_number:
            return _NoWork(
                run_status="PARTIALLY_COMPLETED",
                step_note="No purchase order number in run context",
                skip_note="Skipped — no purchase order",
            )

        from app.models.invoice import Invoice

        invoice = db.execute(
            select(Invoice)
            .where(
                Invoice.po_number == po_number,
                Invoice.match_status.isnot(None),
            )
            .order_by(Invoice.created_at.asc())
            .limit(1)
        ).scalars().first()

        if invoice is None:
            return _NoWork(
                run_status="WAITING_EXTERNAL_INPUT",
                step_note=(
                    f"Waiting for invoice against PO {po_number} to be "
                    f"matched before syncing to Tally"
                ),
                skip_note=f"Skipped — waiting for matched invoice (PO {po_number})",
            )

        approved = bool(
            (invoice.match_result or {}).get(
                "approved_despite_mismatch"
            )
        )

        from orchestrator.chain import _books_payload

        return _books_payload(invoice, approved)

    logger.error("No payload builder for step type %s", task_type)
    return _NoWork(
        run_status="PARTIALLY_COMPLETED",
        step_note=f"No payload builder for step type '{task_type}'",
        skip_note="Skipped — unknown step type",
    )


def _merge_context(
    run: WorkflowRun,
    task_type: str,
    output: dict,
) -> None:
    """Fold document numbers from a finished step into the run context."""
    context = run.context or {}

    summary = output.get("summary") or {}

    for key in ("po_number", "grn_number", "invoice_number"):
        value = output.get(key) or summary.get(key)
        if value:
            context[key] = value

    match = output.get("match_result") or {}
    if match.get("invoice_number"):
        context["invoice_number"] = match["invoice_number"]
    if match.get("po_number"):
        context["po_number"] = match["po_number"]

    # A brand-new dict, not the mutated original: SQLAlchemy does not
    # detect in-place changes to a JSONB column, and assigning the very
    # same object records no difference — the update silently never
    # reaches the database.
    run.context = dict(context)


# ── Lookups ──────────────────────────────────────────────────────────


def _step_run_by_task(
    db: Session,
    task_id: uuid.UUID,
) -> WorkflowStepRun | None:
    return db.execute(
        select(WorkflowStepRun)
        .where(WorkflowStepRun.task_id == task_id)
        .limit(1)
    ).scalars().first()


def _definition(
    db: Session,
    workflow_code: str,
) -> list[WorkflowStep]:
    return db.execute(
        select(WorkflowStep)
        .where(WorkflowStep.workflow_code == workflow_code)
        .order_by(WorkflowStep.position.asc())
    ).scalars().all()
