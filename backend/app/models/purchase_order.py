"""Purchase order tables.

These tables already existed in the database, unused and unmapped. The
column definitions here mirror the live schema exactly — ``create_all``
will not alter an existing table, so a mismatch surfaces as an insert
error at runtime rather than a migration failure at startup.

``purchase_number`` carries a UNIQUE constraint, which is what actually
guarantees one PO number is never issued twice. ``po_number_seq`` makes
collisions unlikely; this constraint makes them impossible.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.agent import Base


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

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

    # UNIQUE: one purchase request raises at most one PO. A retry that
    # tried to raise a second would hit this rather than succeed quietly.
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
    )

    purchase_number: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
    )

    vendor: Mapped[str | None] = mapped_column(
        String,
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

    # CREATED → PARTIALLY_RECEIVED → RECEIVED → INVOICED → POSTED
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )

    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        back_populates="purchase_order",
        cascade="all, delete-orphan",
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id"),
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

    purchase_order: Mapped[PurchaseOrder] = relationship(
        back_populates="items",
    )
