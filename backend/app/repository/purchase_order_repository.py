"""Purchase order persistence.

The PO is the anchor of the Purchase-to-Pay flow: S1 receives goods
against it and P4 matches invoices against it. Everything downstream
loads it from here by ``po_number`` rather than trusting a copy passed
along in a task payload.
"""

import logging
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.documents import (
    PurchaseOrderDocument,
    PurchaseOrderLine,
    to_decimal,
)
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem


logger = logging.getLogger(__name__)


class PurchaseOrderRepository:

    def __init__(self, db: Session):
        self.db = db

    # ── Reads ────────────────────────────────────────────────────────

    def get_row(self, po_number: str) -> PurchaseOrder | None:
        return self.db.execute(
            select(PurchaseOrder)
            .where(PurchaseOrder.purchase_number == po_number)
            .limit(1)
        ).scalars().first()

    def get_by_number(
        self,
        po_number: str,
    ) -> PurchaseOrderDocument | None:
        """The PO as a contract document, or None if never raised."""
        row = self.get_row(po_number)

        if row is None:
            return None

        return self.to_document(row)

    def get_by_task(self, task_id: uuid.UUID) -> PurchaseOrder | None:
        """Used to keep PO creation idempotent across task retries."""
        return self.db.execute(
            select(PurchaseOrder)
            .where(PurchaseOrder.task_id == task_id)
            .limit(1)
        ).scalars().first()

    def to_document(
        self,
        row: PurchaseOrder,
    ) -> PurchaseOrderDocument:
        return PurchaseOrderDocument(
            purchase_id=row.payload.get("purchase_id")
            or row.purchase_number,
            po_number=row.purchase_number,
            vendor=row.vendor or "",
            items=[
                PurchaseOrderLine(
                    item=item.item_name,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    total_amount=item.total_amount,
                )
                for item in row.items
            ],
            total_amount=row.total_amount,
            currency=row.currency,
            status=row.status,
        )

    # ── Writes ───────────────────────────────────────────────────────

    def create(
        self,
        task_id: uuid.UUID,
        correlation_id: uuid.UUID,
        po_number: str,
        vendor: str,
        items: list[dict],
        total_amount: Decimal,
        currency: str = "INR",
        status: str = "CREATED",
        payload: dict | None = None,
    ) -> PurchaseOrder:
        """Persist a PO and its lines.

        No flush/commit here — the caller owns the transaction so the
        document and the task status land together.
        """
        purchase_order = PurchaseOrder(
            id=uuid.uuid4(),
            task_id=task_id,
            correlation_id=correlation_id,
            purchase_number=po_number,
            vendor=vendor,
            total_amount=to_decimal(total_amount),
            currency=currency,
            status=status,
            payload=payload or {},
        )

        for item in items:
            quantity = to_decimal(item.get("quantity"))
            unit_price = to_decimal(item.get("unit_price"))

            purchase_order.items.append(
                PurchaseOrderItem(
                    id=uuid.uuid4(),
                    item_name=item.get("item", ""),
                    quantity=quantity,
                    unit_price=unit_price,
                    total_amount=quantity * unit_price,
                )
            )

        self.db.add(purchase_order)

        return purchase_order

    def set_status(self, po_number: str, status: str) -> str | None:
        """Advance the PO's workflow state. Returns the new status."""
        row = self.get_row(po_number)

        if row is None:
            logger.warning(
                "Cannot set status on unknown PO %s", po_number
            )
            return None

        if row.status == status:
            return status

        logger.info(
            "PO %s status %s -> %s", po_number, row.status, status
        )

        row.status = status

        return status
