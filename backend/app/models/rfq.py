"""RFQ tables — the P2 sourcing working documents.

An RFQ is the document P2 issues to approved vendors; each vendor's
reply lands as a quote row against it. Together they form the
quote-comparison pack the buyer awards from.

``rfq_number`` carries a UNIQUE constraint — the actual guarantee one
RFQ is never issued twice. ``rfq_number_seq`` makes collisions
unlikely; this constraint makes them impossible.

A quote row is written once per (rfq, vendor) and is never updated in
place: a vendor revising a quote writes a new row (``revision``), so
the history of what each vendor actually offered survives the
negotiation.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.agent import Base


class Rfq(Base):
    __tablename__ = "rfqs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id"),
        nullable=False,
    )

    # UNIQUE: one issuing task raises at most one RFQ, so a re-queued
    # or retried task cannot issue a second one to the vendors.
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
    )

    rfq_number: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
    )

    vendor: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    # ISSUED → QUOTES_PENDING → AWARDED → CONVERTED / CANCELLED
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="ISSUED",
    )

    # The vendor the buyer chose. Human-decided — P2 only recommends.
    awarded_vendor: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    quotes: Mapped[list["RfqQuote"]] = relationship(
        back_populates="rfq",
        cascade="all, delete-orphan",
    )


class RfqQuote(Base):
    __tablename__ = "rfq_quotes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rfqs.id"),
        nullable=False,
    )

    # The collect task that filed this quote — the idempotency key
    # that keeps a retried task from appending the same quotes twice.
    source_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id"),
        nullable=True,
    )

    vendor: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    # Vendors may revise; each revision is a new row so the offered
    # history is never overwritten.
    revision: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
    )

    total_amount: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="INR",
    )

    lead_time_days: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    # line-level detail as sent by the vendor, normalised by P2
    lines: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # The agent's own normalisation verdict for this quote, kept with
    # the row: normalised | parse_failed | price_missing.
    parse_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="normalised",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    rfq: Mapped[Rfq] = relationship(
        back_populates="quotes",
    )
