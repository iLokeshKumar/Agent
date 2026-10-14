import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import Approval


class ApprovalRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        approval_type: str,
        task_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
        requested_by: str | None = None,
        payload: dict | None = None,
    ) -> Approval:
        now = datetime.now(timezone.utc)

        approval = Approval(
            id=uuid.uuid4(),
            task_id=task_id,
            agent_run_id=agent_run_id,
            approval_type=approval_type,
            status="PENDING",
            requested_by=requested_by,
            payload=payload or {},
            requested_at=now,
        )

        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)

        return approval

    def get(self, approval_id: uuid.UUID) -> Approval | None:
        return self.db.get(Approval, approval_id)

    def approve(
        self,
        approval: Approval,
        approved_by: str,
        reason: str | None = None,
    ) -> Approval:
        now = datetime.now(timezone.utc)

        approval.status = "APPROVED"
        approval.approved_by = approved_by
        approval.reason = reason
        approval.decided_at = now

        self.db.commit()
        self.db.refresh(approval)

        return approval

    def reject(
        self,
        approval: Approval,
        approved_by: str,
        reason: str | None = None,
    ) -> Approval:
        now = datetime.now(timezone.utc)

        approval.status = "REJECTED"
        approval.approved_by = approved_by
        approval.reason = reason
        approval.decided_at = now

        self.db.commit()
        self.db.refresh(approval)

        return approval

    def list_pending(
        self,
        approval_type: str | None = None,
    ) -> list[Approval]:
        statement = (
            select(Approval)
            .where(Approval.status == "PENDING")
            .order_by(Approval.requested_at.asc())
        )

        if approval_type:
            statement = statement.where(
                Approval.approval_type == approval_type
            )

        return list(self.db.execute(statement).scalars().all())

    def get_pending_by_task(
        self,
        task_id: uuid.UUID,
    ) -> Approval | None:
        statement = (
            select(Approval)
            .where(
                Approval.task_id == task_id,
                Approval.status == "PENDING",
            )
            .order_by(Approval.requested_at.asc())
            .limit(1)
        )

        # .first() — not scalar_one_or_none(). A task can legitimately
        # accumulate more than one approval row (rejected, then
        # re-queued), and raising MultipleResultsFound inside the
        # worker would surface as an unrelated retry storm.
        return self.db.execute(statement).scalars().first()

    def get_approved_by_task(
        self,
        task_id: uuid.UUID,
    ) -> Approval | None:
        """Check if an approval was already granted for this task."""
        statement = (
            select(Approval)
            .where(
                Approval.task_id == task_id,
                Approval.status == "APPROVED",
            )
            .order_by(Approval.decided_at.desc())
            .limit(1)
        )

        return self.db.execute(statement).scalars().first()
