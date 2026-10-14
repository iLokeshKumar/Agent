"""Gateway events — webhook dedupe register.

Webhook providers redeliver (at-least-once semantics). Every accepted
event is registered here inside the same transaction that enqueues the
work, so a redelivery finds the row and is answered "already handled"
instead of spawning a second purchase order. The unique constraint is
the mechanism, not discipline.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base

# Portable payload column: plain JSON everywhere for now.
PortableJSON = JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class GatewayEvent(Base):
    __tablename__ = "gateway_events"
    __table_args__ = (
        UniqueConstraint("source", "event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # e.g. "waba", "zoho_books", "graph"
    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # Provider's event id; when absent, a hash of the payload.
    event_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # received | dispatched | duplicate | rejected
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="received",
    )

    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    payload: Mapped[dict] = mapped_column(
        PortableJSON,
        nullable=False,
        server_default="{}",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
