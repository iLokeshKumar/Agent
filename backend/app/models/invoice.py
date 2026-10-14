"""Supplier invoice tables — the third leg of the three-way match.

The invoice is the one document the platform cannot derive: the PO says
what was ordered and the GRN says what arrived, but only the supplier
knows what is being billed. It is therefore always supplied from
outside — either attached to the purchase request (the tax invoice
usually travels with the goods) or posted later as its own task.
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


class Invoice(Base):
    __tablename__ = "invoices"

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

    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # UNIQUE: the supplier's own invoice number. Registering the same
    # invoice twice is double-billing, so it fails at the constraint.
    invoice_number: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
    )

    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id"),
        nullable=True,
    )

    po_number: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
    )

    goods_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goods_receipts.id"),
        nullable=True,
    )

    vendor: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    invoice_amount: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'INR'"),
    )

    invoice_date: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # RECEIVED → MATCHED | EXCEPTION → POSTED
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'RECEIVED'"),
    )

    # FULLY_MATCHED | PARTIALLY_MATCHED | MISMATCH — null until matched
    match_status: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    match_result: Mapped[dict | None] = mapped_column(
        JSONB,
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
        server_default=text("now()"),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )

    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id"),
        nullable=False,
    )

    item_name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
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

    invoice: Mapped[Invoice] = relationship(
        back_populates="items",
    )
