import uuid
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AgentResult:
    """Standard result returned by all agents."""
    status: str  # SUCCESS, FAILED, PARTIAL, VALIDATION_ERROR
    output: dict = field(default_factory=dict)
    error: str | None = None
    actions_taken: list[str] = field(default_factory=list)


class BaseAgent(ABC):
    """Abstract base class for all agent implementations.

    Provides:
        - Standard lifecycle: validate → execute → log
        - Shared idempotency: check ledger before executing
        - Shared ledger recording: record actions after execution

    Each concrete agent must implement:
        - validate(payload)  — check input is well-formed
        - execute(payload, context) — do the actual work
        - describe() — human-readable description
    """

    agent_code: str = ""
    agent_name: str = ""

    def __init__(self):
        self.logger = logging.getLogger(
            f"agent.{self.agent_code}"
        )

    def run(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        """Full lifecycle: validate → execute → log."""
        self.logger.info(
            "Agent %s starting task %s",
            self.agent_code,
            task_id,
        )

        # Validate — a validator that itself chokes on hostile input
        # must fail closed as VALIDATION_ERROR, not blow up the caller.
        try:
            validation = self.validate(payload)
        except Exception as exc:
            self.logger.warning(
                "Validator raised on payload: %s", exc
            )
            validation = f"Payload malformed: {exc}"

        if validation is not None:
            self.logger.warning(
                "Validation failed: %s", validation
            )
            return AgentResult(
                status="VALIDATION_ERROR",
                error=validation,
            )

        # Execute
        try:
            result = self.execute(
                payload,
                task_id=task_id,
                requested_by=requested_by,
            )
            self.logger.info(
                "Agent %s completed: %s",
                self.agent_code,
                result.status,
            )
            return result

        except Exception as exc:
            self.logger.exception(
                "Agent %s failed", self.agent_code
            )
            return AgentResult(
                status="FAILED",
                error=str(exc),
            )

    def _approval_granted(
        self,
        task_id: uuid.UUID | None,
    ) -> bool:
        """True if a human has already approved this task.

        An agent that returns NEEDS_APPROVAL must call this on its next
        run, because the executor re-queues the task after approval and
        the underlying facts have not changed — the budget is still
        exceeded, the invoice still disagrees with the PO. Without this
        check the agent would ask again and the executor would fail the
        task with APPROVAL_NOT_HONOURED.
        """
        if task_id is None:
            return False

        from app.core.db import SessionLocal
        from app.repository.approval_repository import (
            ApprovalRepository,
        )

        db = SessionLocal()

        try:
            return (
                ApprovalRepository(db).get_approved_by_task(task_id)
                is not None
            )
        finally:
            db.close()

    def execute_with_idempotency(
        self,
        db,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
        correlation_id: uuid.UUID | None = None,
    ) -> AgentResult:
        """Execute with idempotency check.

        If a ledger entry already exists for this correlation_id,
        return the previous result. Otherwise, execute and record.

        This is the recommended entry point for all agents.
        """
        # Lazy import to avoid circular dependencies
        from app.models.action_ledger import ActionLedger

        # ── Idempotency check ────────────────────────────────────────
        if correlation_id:
            existing = (
                db.query(ActionLedger)
                .filter(
                    ActionLedger.correlation_id == correlation_id,
                    ActionLedger.action == "TASK_EXECUTION",
                    ActionLedger.status == "SUCCESS",
                )
                .first()
            )

            if existing is not None:
                self.logger.info(
                    "%s: idempotent hit for correlation %s",
                    self.agent_code,
                    correlation_id,
                )
                return AgentResult(
                    status="SUCCESS",
                    output=existing.output or {},
                    actions_taken=["idempotent_return"],
                )

        # ── Normal execution ─────────────────────────────────────────
        result = self.run(
            payload=payload,
            task_id=task_id,
            requested_by=requested_by,
        )

        # ── Record in action ledger ──────────────────────────────────
        if result.status == "SUCCESS" and correlation_id:
            self._record_ledger(
                db=db,
                correlation_id=correlation_id,
                task_id=task_id,
                result=result,
            )

        return result

    def _record_ledger(
        self,
        db,
        correlation_id: uuid.UUID,
        task_id: uuid.UUID | None,
        result: AgentResult,
    ) -> None:
        """Record the action in the ledger."""
        from app.models.action_ledger import ActionLedger

        ledger = ActionLedger(
            task_id=task_id,
            action="TASK_EXECUTION",
            entity_type=self.agent_code.lower(),
            entity_id=result.output.get(
                "po_number",
                result.output.get(
                    "grn_number",
                    result.output.get(
                        "ticket_number",
                        result.output.get(
                            "query_type",
                            "",
                        ),
                    ),
                ),
            ),
            tool_name=self.agent_code,
            status="SUCCESS",
            correlation_id=correlation_id,
        )

        db.add(ledger)
        db.commit()

        self.logger.info(
            "%s: ledger entry recorded",
            self.agent_code,
        )

    # ── Abstract methods (must be implemented by each agent) ─────────

    @abstractmethod
    def validate(self, payload: dict) -> str | None:
        """Return error message if invalid, None if OK."""
        ...

    @abstractmethod
    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        """Run the agent logic and return a result."""
        ...

    @abstractmethod
    def describe(self) -> str:
        """Return a human-readable description."""
        ...
