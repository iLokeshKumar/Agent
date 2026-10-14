import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )

    task_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    source: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    requested_by: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    error: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    
    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )
    
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=300,
    )