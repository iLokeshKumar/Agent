"""Goods receipt tables — the document S1 publishes.

Shaped like ``purchase_orders`` deliberately: same task/correlation
linkage, same ``payload`` jsonb holding the full document, same
timestamps. One difference that matters — ``correlation_id`` is **not**
unique here. A purchase order is legitimately received in several
deliveries, so one PO has many GRNs under the same workflow.
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


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

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

    grn_number: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
    )

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id"),
        nullable=False,
    )

    # Denormalised from the PO so audit queries and P4 lookups read
    # naturally without a join. Immutable once written.
    po_number: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
    )

    supplier: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    # COMPLETE | PARTIAL | OVER_RECEIVED | HAS_DISCREPANCIES
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    # Echoed from the delivery note as supplied (YYYY-MM-DD). Kept as
    # text so a malformed date on a paper note cannot fail the receipt.
    delivery_date: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    receiving_person: Mapped[str | None] = mapped_column(
        String,
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

    items: Mapped[list["GoodsReceiptItem"]] = relationship(
        back_populates="goods_receipt",
        cascade="all, delete-orphan",
    )


class GoodsReceiptItem(Base):
    __tablename__ = "goods_receipt_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    goods_receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goods_receipts.id"),
        nullable=False,
    )

    item_name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
    )

    ordered_quantity: Mapped[Decimal] = mapped_column(
        Numeric,
        nullable=False,
    )

    received_quantity: Mapped[Decimal] = mapped_column(
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

    condition: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'GOOD'"),
    )

    qty_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'MATCH'"),
    )

    goods_receipt: Mapped[GoodsReceipt] = relationship(
        back_populates="items",
    )

    serials: Mapped[list["GoodsReceiptSerial"]] = relationship(
        back_populates="goods_receipt_item",
        cascade="all, delete-orphan",
    )


class GoodsReceiptSerial(Base):
    """One serial number captured at receipt.

    Not UNIQUE: a unit returned and re-received under an RMA carries the
    same serial, and refusing that would block a legitimate receipt. S1
    looks for an earlier row and raises a DUPLICATE_SERIAL discrepancy
    instead, so the receipt is recorded *and* the problem is visible.
    """

    __tablename__ = "goods_receipt_serials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    goods_receipt_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goods_receipt_items.id"),
        nullable=False,
    )

    item_name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    serial_number: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    goods_receipt_item: Mapped[GoodsReceiptItem] = relationship(
        back_populates="serials",
    )
