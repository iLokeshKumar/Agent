import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.action_ledger import ActionLedger
from app.models.agent import Agent
from app.models.task import Task
from app.core.policy import PolicyGate
from app.repository.approval_repository import ApprovalRepository
from app.repository.lane_repository import LaneDecision, LaneRepository
from app.services.documents import record_document
from app.agents import get_agent
from app.agents.base import AgentResult
from orchestrator.chain import advance_chain
from orchestrator.router import route_task


logger = logging.getLogger(__name__)


def _run_in_dry_run(agent_impl, payload: dict) -> AgentResult:
    """Validate a proposed action without invoking agent-side tools."""
    validation_error = agent_impl.validate(payload)
    if validation_error:
        return AgentResult(
            status="VALIDATION_ERROR",
            error=validation_error,
        )

    return AgentResult(
        status="SUCCESS",
        output={
            "dry_run": True,
            "proposal": "Input accepted; execution suppressed by lane policy.",
        },
        actions_taken=["validated_input", "execution_suppressed"],
    )


def _refuse_by_lane(
    db: Session,
    task: Task,
    agent: Agent,
    reason: str,
    error_code: str,
) -> dict:
    """Fail a task the lane refuses to run, leaving an audit mark."""
    task.status = "FAILED"
    task.error = {"error": reason}
    task.completed_at = datetime.now(timezone.utc)

    db.add(ActionLedger(
        task_id=task.id,
        agent_id=agent.id,
        action="TASK_EXECUTION",
        entity_type="task",
        entity_id=str(task.id),
        tool_name=agent.agent_code,
        approval_status="NOT_REQUIRED",
        status="BLOCKED",
        error_code=error_code,
        error_message=reason,
        correlation_id=task.correlation_id,
    ))
    db.commit()

    logger.warning("Task %s refused by lane %s: %s",
                   task.id, agent.agent_code, reason)

    return {"status": "FAILED", "error": reason}


def execute_task(
    db: Session,
    task: Task,
) -> dict:

    now = datetime.now(timezone.utc)

    #task.status = "RUNNING"
    task.started_at = now

    db.commit()

    agent_code = route_task(task.task_type)

    agent = (
        db.query(Agent)
        .filter(
            Agent.agent_code == agent_code,
            Agent.enabled.is_(True),
        )
        .first()
    )

    if agent is None:
        raise RuntimeError(
            f"Agent {agent_code} is not available"
        )

    # --- Lane governance (kill switch · quiet hours · daily cap) ---
    lane_repo = LaneRepository(db)
    lane_decision: LaneDecision = lane_repo.evaluate(
        agent.agent_code, priority=task.priority or 100
    )

    if not lane_decision.allowed:
        error_code = (
            "LANE_KILLED" if "killed" in lane_decision.reason
            else "QUIET_HOURS"
        )
        return _refuse_by_lane(
            db, task, agent, lane_decision.reason, error_code
        )

    if lane_decision.lane is not None and lane_repo.cap_exceeded(
        lane_decision.lane
    ):
        reason = (
            f"Lane {agent.agent_code} hit its daily cap "
            f"({lane_decision.lane.daily_cap}/day)"
        )
        return _refuse_by_lane(db, task, agent, reason, "DAILY_CAP_EXCEEDED")

    dry_run = lane_decision.dry_run

    # --- Policy Gate (6-step check) ---
    policy = PolicyGate(db)

    decision = policy.evaluate(
        agent=agent,
        action="TASK_EXECUTION",
        task=task,
        requested_by=task.requested_by,
    )

    # Step 6 outcome: DENY → FAILED
    if decision.decision == "DENY":
        task.status = "FAILED"
        task.error = {"error": decision.reason}
        db.commit()

        logger.warning(
            "Task %s denied by Policy Gate: %s",
            task.id, decision.reason,
        )

        return {
            "status": "FAILED",
            "error": decision.reason,
        }

    # Step 6 outcome: REQUIRE_APPROVAL → WAITING_APPROVAL
    if decision.decision == "REQUIRE_APPROVAL":
        approved = policy.ensure_approval(
            decision=decision,
            task=task,
            agent=agent,
            requested_by=task.requested_by,
        )

        if not approved:
            task.status = "AWAITING_APPROVAL"
            db.commit()

            logger.info(
                "Task %s paused: awaiting approval for %s",
                task.id, decision.approval_type,
            )

            return {
                "status": "AWAITING_APPROVAL",
                "approval_type": decision.approval_type,
                "reason": decision.reason,
            }

    # Step 6 outcome: ALLOW → EXECUTE

    # --- Run the real agent ---
    agent_impl = get_agent(agent.agent_code)

    if agent_impl is None:
        raise RuntimeError(
            f"No implementation for agent {agent.agent_code}"
        )

    agent_result = (
        _run_in_dry_run(agent_impl, task.payload)
        if dry_run
        else agent_impl.run(
            payload=task.payload,
            task_id=task.id,
            requested_by=task.requested_by,
        )
    )

    # --- Agent requested approval (e.g. over budget) ---
    if agent_result.status == "NEEDS_APPROVAL":
        approval_repo = ApprovalRepository(db)

        granted = approval_repo.get_approved_by_task(task.id)

        if granted is not None:
            # Approval was already granted, yet the agent still
            # refuses to proceed. Never fall through to COMPLETED
            # here — that would close the task with no PO created.
            reason = (
                "Agent returned NEEDS_APPROVAL even though approval "
                f"{granted.id} is already APPROVED — no action taken"
            )

            task.status = "FAILED"
            task.error = {"error": reason}
            task.completed_at = datetime.now(timezone.utc)

            db.add(ActionLedger(
                task_id=task.id,
                agent_id=agent.id,
                action="TASK_EXECUTION",
                entity_type="task",
                entity_id=str(task.id),
                tool_name=agent.agent_code,
                approval_status="APPROVED",
                status="FAILED",
                error_code="APPROVAL_NOT_HONOURED",
                error_message=reason,
                correlation_id=task.correlation_id,
            ))
            db.commit()

            logger.error("Task %s: %s", task.id, reason)

            return {"status": "FAILED", "error": reason}

        # Reuse an existing PENDING approval rather than creating a
        # duplicate — a re-queued task lands here again.
        approval = approval_repo.get_pending_by_task(task.id)

        # The agent describes its own approval. P1's budget gate is the
        # default so its lifecycle is untouched, while P4 raises an
        # INVOICE_MISMATCH through the same branch.
        approval_type = (
            agent_result.output.get("approval_type")
            or "BUDGET_EXCEEDED"
        )

        approval_payload = agent_result.output.get("approval_payload")

        if approval_payload is None:
            approval_payload = {
                "total_amount": (
                    agent_result.output.get("total_amount")
                ),
                "budget_limit": (
                    agent_result.output.get("budget_limit")
                ),
                "vendor": agent_result.output.get("vendor"),
            }

        if approval is None:
            approval = approval_repo.create(
                approval_type=approval_type,
                task_id=task.id,
                requested_by=task.requested_by,
                payload={
                    "task_type": task.task_type,
                    "agent_code": agent.agent_code,
                    "reason": agent_result.output.get("reason"),
                    **approval_payload,
                },
            )
            logger.info(
                "Task %s paused: %s, approval %s created",
                task.id, approval_type, approval.id,
            )
        else:
            logger.info(
                "Task %s paused: approval %s already pending",
                task.id, approval.id,
            )

        task.status = "AWAITING_APPROVAL"
        task.result = {
            "status": "AWAITING_APPROVAL",
            "approval_id": str(approval.id),
            "reason": agent_result.output.get("reason"),
        }

        # Record the pause so the audit trail shows why nothing
        # happened, not just a gap between rows.
        db.add(ActionLedger(
            task_id=task.id,
            agent_id=agent.id,
            action="TASK_EXECUTION",
            entity_type="task",
            entity_id=str(task.id),
            tool_name=agent.agent_code,
            approval_status="PENDING",
            status="BLOCKED",
            error_code="APPROVAL_REQUIRED",
            error_message=agent_result.output.get("reason"),
            correlation_id=task.correlation_id,
        ))
        db.commit()

        # The run must show why nothing is moving.
        from orchestrator.workflow_engine import on_task_paused

        try:
            if on_task_paused(db, task):
                db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Could not record approval pause on workflow for "
                "task %s",
                task.id,
            )

        return {
            "status": "AWAITING_APPROVAL",
            "approval_id": str(approval.id),
            "reason": agent_result.output.get("reason"),
        }

    result = {
        "status": agent_result.status,
        "agent_code": agent.agent_code,
        "agent_name": agent.name,
        "task_type": task.task_type,
        "autonomy_state": lane_decision.autonomy_state,
        "dry_run": dry_run,
        "output": agent_result.output,
        "actions_taken": agent_result.actions_taken,
    }

    if agent_result.error:
        result["error"] = agent_result.error

    # A human approval may have come from the agent's own budget gate
    # rather than from the Policy Gate, so check the approvals table
    # too — otherwise an approved run is logged as NOT_REQUIRED.
    if decision.decision == "REQUIRE_APPROVAL":
        approval_status = "APPROVED"
    elif ApprovalRepository(db).get_approved_by_task(task.id):
        approval_status = "APPROVED"
    else:
        approval_status = "NOT_REQUIRED"

    # The ledger must reflect what actually happened. Hardcoding
    # SUCCESS here logged failed runs as successful.
    ledger_status = (
        "SUCCESS"
        if agent_result.status in ("SUCCESS", "PARTIAL")
        else "FAILED"
    )

    ledger_entry = ActionLedger(
        task_id=task.id,
        agent_id=agent.id,
        action="TASK_EXECUTION",
        entity_type="task",
        entity_id=(
            agent_result.output.get("po_number")
            or str(task.id)
        ),
        tool_name=agent.agent_code,
        approval_status=approval_status,
        status=ledger_status,
        error_code=(
            agent_result.status
            if ledger_status == "FAILED"
            else None
        ),
        error_message=agent_result.error,
        correlation_id=task.correlation_id,
    )

    db.add(ledger_entry)

    # Set task status based on agent result
    if agent_result.status == "FAILED":
        task.status = "FAILED"
        task.error = {"error": agent_result.error}
    elif agent_result.status == "VALIDATION_ERROR":
        task.status = "VALIDATION_ERROR"
        task.error = {"error": agent_result.error}
    else:
        task.status = "COMPLETED"

    task.result = result
    task.completed_at = datetime.now(timezone.utc)

    db.commit()

    if task.status in ("FAILED", "VALIDATION_ERROR"):
        # A workflow-managed failure fails the run; remaining steps are
        # skipped so the state never reads as merely paused.
        from orchestrator.workflow_engine import on_task_failed

        try:
            if on_task_failed(db, task, agent_result.error):
                db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Could not record failure on workflow for task %s",
                task.id,
            )

    if task.status == "COMPLETED" and not dry_run:
        _post_completion(
            db, task, agent.agent_code, agent_result.output
        )
    elif dry_run:
        # Dry-run: the agent ran and reported, the ledger and task
        # state are already committed — but no document row is written
        # and no chain/workflow step fires. The run leaves exactly one
        # trace: "what the agent would have done".
        logger.info(
            "Task %s completed in DRY RUN — document commit "
            "suppressed",
            task.id,
        )

    return result


def _post_completion(
    db: Session,
    task: Task,
    agent_code: str,
    output: dict,
) -> None:
    """Persist the business document, then queue the next step.

    Wrapped because a fault here must never reach the worker:
    ``worker.py`` catches any exception out of ``execute_task`` and calls
    ``fail_or_retry()``, which would put this already-COMPLETED task back
    on the queue — re-running the agent and issuing a second purchase
    order for the same request. A failed hook leaves the task COMPLETED
    with no document and a stack trace in the log, which is recoverable;
    a duplicate PO is not.
    """
    try:
        record_document(db, task, agent_code, output or {})

        from orchestrator.workflow_engine import on_task_completed

        # A workflow-managed task advances its run; the hardcoded chain
        # only fires for tasks that no workflow owns. Both must never
        # run for the same task — that is how children get spawned twice.
        workflow_managed = on_task_completed(
            db, task, output or {}
        )

        if not workflow_managed:
            advance_chain(db, task, output or {})

        db.commit()

    except Exception:
        logger.exception(
            "Post-completion hook failed for task %s (%s) — task stays "
            "COMPLETED, no document recorded and no next step queued",
            task.id,
            agent_code,
        )
        db.rollback()
