"""Supplier invoice persistence.

An invoice can enter the workflow two ways — attached to the purchase
request (the tax invoice usually travels with the goods) or posted later
as its own ``invoice_matching`` task. Either way it lands here first, so
by the time P4 runs there is a stored document to match against rather
than a number lifted out of a task payload.
"""

import logging
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.documents import (
    InvoiceDocument,
    InvoiceLine,
    to_decimal,
)
from app.models.invoice import Invoice, InvoiceItem


logger = logging.getLogger(__name__)


class InvoiceRepository:

    def __init__(self, db: Session):
        self.db = db

    # ── Reads ────────────────────────────────────────────────────────

    def get_row(self, invoice_number: str) -> Invoice | None:
        return self.db.execute(
            select(Invoice)
            .where(Invoice.invoice_number == invoice_number)
            .limit(1)
        ).scalars().first()

    def get_by_number(
        self,
        invoice_number: str,
    ) -> InvoiceDocument | None:
        row = self.get_row(invoice_number)

        return None if row is None else self.to_document(row)

    def get_unmatched_for_po(self, po_number: str) -> Invoice | None:
        """Oldest invoice for this PO that has not been matched yet.

        The chain calls this to decide whether a three-way match can run
        at all. None means the supplier has not billed us — the chain
        stops there rather than inventing an amount to match against.
        """
        return self.db.execute(
            select(Invoice)
            .where(
                Invoice.po_number == po_number,
                Invoice.match_status.is_(None),
            )
            .order_by(Invoice.created_at.asc())
            .limit(1)
        ).scalars().first()

    def to_document(self, row: Invoice) -> InvoiceDocument:
        return InvoiceDocument(
            invoice_number=row.invoice_number,
            po_number=row.po_number,
            vendor=row.vendor,
            invoice_amount=row.invoice_amount,
            currency=row.currency,
            invoice_date=row.invoice_date,
            items=[
                InvoiceLine(
                    item=item.item_name,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    total_amount=item.total_amount,
                )
                for item in row.items
            ],
            status=row.status,
            match_status=row.match_status,
        )

    # ── Writes ───────────────────────────────────────────────────────

    def create(
        self,
        task_id: uuid.UUID,
        correlation_id: uuid.UUID,
        invoice_number: str,
        po_number: str,
        invoice_amount: Decimal,
        purchase_order_id: uuid.UUID | None = None,
        vendor: str | None = None,
        currency: str = "INR",
        invoice_date: str | None = None,
        items: list[dict] | None = None,
        status: str = "RECEIVED",
        payload: dict | None = None,
    ) -> Invoice:
        """Register a supplier invoice. No commit — caller's transaction."""
        invoice = Invoice(
            id=uuid.uuid4(),
            task_id=task_id,
            correlation_id=correlation_id,
            invoice_number=invoice_number,
            purchase_order_id=purchase_order_id,
            po_number=po_number,
            vendor=vendor,
            invoice_amount=to_decimal(invoice_amount),
            currency=currency,
            invoice_date=invoice_date,
            status=status,
            payload=payload or {},
        )

        for item in items or []:
            quantity = to_decimal(item.get("quantity"))
            unit_price = to_decimal(item.get("unit_price"))

            invoice.items.append(
                InvoiceItem(
                    id=uuid.uuid4(),
                    item_name=item.get("item", ""),
                    quantity=quantity,
                    unit_price=unit_price,
                    total_amount=to_decimal(
                        item.get("total_amount")
                    ) or quantity * unit_price,
                )
            )

        self.db.add(invoice)

        return invoice

    def record_match(
        self,
        invoice_number: str,
        match_status: str,
        match_result: dict,
        goods_receipt_id: uuid.UUID | None = None,
    ) -> Invoice | None:
        """Write the three-way match outcome onto the invoice."""
        row = self.get_row(invoice_number)

        if row is None:
            logger.warning(
                "Cannot record match on unknown invoice %s",
                invoice_number,
            )
            return None

        row.match_status = match_status
        row.match_result = match_result
        row.status = (
            "MATCHED"
            if match_status == "FULLY_MATCHED"
            else "EXCEPTION"
        )

        if goods_receipt_id is not None:
            row.goods_receipt_id = goods_receipt_id

        logger.info(
            "Invoice %s recorded as %s",
            invoice_number,
            match_status,
        )

        return row

    def mark_posted(self, invoice_number: str) -> Invoice | None:
        """Final state: the invoice has reached the books."""
        row = self.get_row(invoice_number)

        if row is not None:
            row.status = "POSTED"

        return row
