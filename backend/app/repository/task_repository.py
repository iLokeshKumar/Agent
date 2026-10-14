from datetime import datetime, timezone, timedelta
import uuid
import logging

from sqlalchemy import select, or_, and_, text
from sqlalchemy.orm import Session

from app.models.task import Task


logger = logging.getLogger(__name__)


class TaskRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        task_type: str,
        source: str | None,
        priority: int,
        requested_by: str | None,
        payload: dict,
        correlation_id: uuid.UUID | None = None,
    ) -> Task:
        """Queue a task.

        ``correlation_id`` defaults to a fresh one — a task arriving from
        outside starts its own trace. A chained child passes the
        parent's, so one purchase request reads as a single workflow
        across every document it produces.
        """
        now = datetime.now(timezone.utc)

        task = Task(
            id=uuid.uuid4(),
            task_type=task_type,
            source=source,
            priority=priority,
            status="QUEUED",
            requested_by=requested_by,
            payload=payload,
            correlation_id=correlation_id or uuid.uuid4(),
            created_at=now,
            retry_count=0,
            max_retries=3,
            timeout_seconds=300,
        )

        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

        return task

    def get(self, task_id: uuid.UUID) -> Task | None:
        return self.db.get(Task, task_id)

    def claim_next_task(self) -> Task | None:
        now = datetime.now(timezone.utc)

        statement = (
            select(Task)
            .where(
                Task.status == "QUEUED",
                or_(
                    Task.next_retry_at.is_(None),
                    Task.next_retry_at <= now,
                ),
            )
            .order_by(
                Task.priority.asc(),
                Task.created_at.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )

        task = self.db.execute(statement).scalar_one_or_none()

        if task is None:
            return None

        task.status = "RUNNING"
        task.started_at = now

        self.db.commit()
        self.db.refresh(task)

        return task

    def list_tasks(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Task]:

        statement = (
            select(Task)
            .order_by(Task.created_at.desc())
            .limit(limit)
        )

        if status:
            statement = statement.where(
                Task.status == status
            )

        return list(
            self.db.execute(statement).scalars().all()
        )
    
    def fail_or_retry(
        self,
        task: Task,
        error: str,
    ) -> str:

        task.last_error = error

        if task.retry_count < task.max_retries:
            task.retry_count += 1

            delay_seconds = 2 ** task.retry_count

            task.next_retry_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=delay_seconds)
            )

            task.status = "QUEUED"

            self.db.commit()

            logger.info(
                "Task %s scheduled for retry %d/%d at %s",
                task.id,
                task.retry_count,
                task.max_retries,
                task.next_retry_at,
            )

            return "RETRY"

        task.status = "FAILED"
        task.completed_at = datetime.now(timezone.utc)

        self.db.commit()

        logger.warning(
            "Task %s exhausted all %d retries. Final error: %s",
            task.id,
            task.max_retries,
            error,
        )

        return "FAILED"

    def recover_stale_tasks(
        self,
    ) -> list[Task]:
        """Find running tasks whose configured execution timeout has elapsed.

        Returns tasks that need recovery so the caller can
        decide whether to retry or fail them.
        """
        now = datetime.now(timezone.utc)

        statement = (
            select(Task)
            .where(
                Task.status == "RUNNING",
                Task.started_at.isnot(None),
            )
            .with_for_update(skip_locked=True)
        )

        running_tasks = self.db.execute(statement).scalars().all()
        stale_tasks = []
        for task in running_tasks:
            started_at = task.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            elapsed = (now - started_at).total_seconds()
            if elapsed >= task.timeout_seconds:
                stale_tasks.append(task)

        return stale_tasks

    def timeout_task(
        self,
        task: Task,
    ) -> str:
        """Handle a timed-out task: retry or fail it."""
        now = datetime.now(timezone.utc)
        elapsed = (
            now - task.started_at
        ).total_seconds() if task.started_at else 0

        error = (
            f"TIMEOUT: task exceeded {task.timeout_seconds}s "
            f"(elapsed {elapsed:.0f}s)"
        )

        logger.warning(
            "Task %s timed out after %.0fs",
            task.id,
            elapsed,
        )

        return self.fail_or_retry(task, error)

    def requeue_task(self, task: Task) -> str:
        """Re-queue an AWAITING_APPROVAL or FAILED task.

        Resets retry counters so it runs fresh.
        """
        valid_statuses = {
            "AWAITING_APPROVAL",
            "FAILED",
            "REJECTED",
            "VALIDATION_ERROR",
        }

        if task.status not in valid_statuses:
            logger.warning(
                "Cannot requeue task %s in status %s",
                task.id,
                task.status,
            )
            return "NOOP"

        previous_status = task.status

        task.status = "QUEUED"
        task.error = None
        task.retry_count = 0
        task.next_retry_at = None
        task.started_at = None

        self.db.commit()

        logger.info(
            "Task %s re-queued (was %s)",
            task.id,
            previous_status,
        )

        return "REQUEUED"

    def fail_task(
        self,
        task: Task,
        reason: str,
    ) -> str:
        """Immediately fail a task (used when approval is rejected)."""
        task.status = "FAILED"
        task.last_error = reason
        # Mirror into `error` as well — that is the column the task
        # API and the lifecycle checks read.
        task.error = {"error": reason}
        task.completed_at = datetime.now(timezone.utc)

        self.db.commit()

        logger.info(
            "Task %s failed: %s",
            task.id,
            reason,
        )

        return "FAILED"

    def reject_task(
        self,
        task: Task,
        reason: str,
    ) -> str:
        """Close a task as a normal human business decision.

        A declined budget exception or invoice mismatch is an expected
        outcome, unlike a connector, validation, or code failure.
        """
        task.status = "REJECTED"
        task.last_error = reason
        task.error = {"error": reason, "classification": "BUSINESS_REJECTION"}
        task.completed_at = datetime.now(timezone.utc)
        self.db.commit()

        logger.info("Task %s rejected by approver: %s", task.id, reason)
        return "REJECTED"

    def recover_timed_out_tasks(self) -> int:
        now = datetime.now(timezone.utc)

        result = self.db.execute(
            text("""
                UPDATE tasks
                SET
                    last_error = 'Task execution timeout',
                    retry_count =
                        CASE
                            WHEN retry_count < max_retries
                            THEN retry_count + 1
                            ELSE retry_count
                        END,
                    status =
                        CASE
                            WHEN retry_count < max_retries
                            THEN 'QUEUED'
                            ELSE 'FAILED'
                        END,
                    next_retry_at =
                        CASE
                            WHEN retry_count < max_retries
                            THEN NOW() +
                                 ((2 ^ (retry_count + 1)) * INTERVAL '1 second')
                            ELSE NULL
                        END,
                    completed_at =
                        CASE
                            WHEN retry_count >= max_retries
                            THEN NOW()
                            ELSE NULL
                        END
                WHERE status = 'RUNNING'
                  AND started_at IS NOT NULL
                  AND started_at +
                      (timeout_seconds * INTERVAL '1 second') < NOW()
            """)
        )

        self.db.commit()

        return result.rowcount

        
