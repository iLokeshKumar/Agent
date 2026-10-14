"""Sync watermarks — cursor state for incremental pulls.

Each integration lane remembers how far it read (Zoho day-book tick,
Graph delta link, ...). A lane that crashes mid-run resumes from its
watermark instead of re-importing a day of vouchers.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base

# JSONB on Postgres; plain JSON elsewhere (SQLite contract tests).
PortableJSON = JSONB().with_variant(JSON(), "sqlite")


class SyncWatermark(Base):
    __tablename__ = "sync_watermark"
    __table_args__ = (
        UniqueConstraint("lane", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # e.g. "F3" — which lane owns this cursor.
    lane: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # e.g. "zoho_books:journals" — what is being read.
    source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Opaque to the platform: a timestamp, sequence number, delta link.
    cursor: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    extra: Mapped[dict] = mapped_column(
        PortableJSON,
        nullable=False,
        server_default=text("'{}'"),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
