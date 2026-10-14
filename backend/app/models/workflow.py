"""Workflow tables — the workflow as a first-class object.

Until now the multi-step flow lived hardcoded in ``chain.py``: one
completed task knew, in Python, which single child to spawn next. That
works for exactly one happy path and cannot express a definition like
``procure_to_pay`` with five named steps.

These three tables move the sequence into the database:

    workflow_steps      the definition — named steps in order
    workflow_runs       one execution of a definition (one trace)
    workflow_step_runs  each step's state within a run, linked to its task

The engine reads the definition and drives the run; adding a step is a
row, not a deploy.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class WorkflowStep(Base):
    """One named step of a workflow definition.

    ``task_type`` is what the router dispatches (purchase_request,
    grn_request, ...). NULL means an engine-managed checkpoint — e.g.
    the approval gate, which completes when no approval is outstanding
    rather than by running an agent.
    """

    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("workflow_code", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )

    workflow_code: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    step_key: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    task_type: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    params: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class WorkflowRun(Base):
    """One execution of a workflow definition.

    The whole run shares one ``correlation_id``, so every task it
    spawns — and every document those tasks produce — reads as a
    single trace, exactly like the chained flow did.
    """

    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )

    workflow_code: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
    )

    # RUNNING → COMPLETED | FAILED
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    current_position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    requested_by: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # Starts as the request payload; each completed step merges its
    # document numbers in, so later steps build payloads from facts.
    context: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    error: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class WorkflowStepRun(Base):
    """One step's state within one run.

    A step run that spawned work carries its ``task_id``; the executor
    finds the step by that link when the task settles.
    """

    __tablename__ = "workflow_step_runs"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_runs.id"),
        nullable=False,
    )

    workflow_step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_steps.id"),
        nullable=False,
    )

    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    step_key: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    task_type: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # PENDING → RUNNING → COMPLETED | SKIPPED | FAILED
    #                ↘ WAITING_APPROVAL → RUNNING
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default="PENDING",
    )

    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    output: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
