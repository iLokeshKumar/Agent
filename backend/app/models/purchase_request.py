"""Purchase request tables.

The purchase request used to live only inside a task's JSONB payload —
invisible to SQL, unqueryable for audit, and gone the moment someone
looked at the business rather than the platform. These rows are
persisted at intake, before the policy gate runs, so every request that
enters the building exists as a document whether it is approved,
rejected, or still waiting.

The link downstream is ``correlation_id``: the PO raised from this
request carries the same value (UNIQUE on both sides), so one join —
no schema change to the existing purchase_orders table, whose columns
mirror a live database.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.agent import Base


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id"),
        nullable=False,
        unique=True,
    )

    # UNIQUE: one workflow trace raises at most one request. Same
    # backstop pattern as purchase_orders.correlation_id.
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
    )

    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id"),
        nullable=True,
    )

    # Denormalised name so audit queries never need the vendors join.
    vendor_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    requested_by: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    budget_limit: Mapped[Decimal | None] = mapped_column(
        Numeric,
        nullable=True,
    )

    total_amount: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'INR'"),
    )

    # SUBMITTED → APPROVED | REJECTED → CONVERTED (a PO was raised)
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
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

    items: Mapped[list["PurchaseRequestItem"]] = relationship(
        back_populates="purchase_request",
        cascade="all, delete-orphan",
    )


class PurchaseRequestItem(Base):
    __tablename__ = "purchase_request_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )

    purchase_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_requests.id"),
        nullable=False,
    )

    item_name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )

    total_amount: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )

    purchase_request: Mapped[PurchaseRequest] = relationship(
        back_populates="items",
    )
