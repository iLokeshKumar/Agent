"""Goods receipt persistence.

``cumulative_received`` is the important one: it sums receipts across
**all** GRNs for a purchase order, which is what makes partial
deliveries work. Ten laptops ordered, six received on Tuesday and four
next month is two GRNs, one PO, and a PO status that only reaches
RECEIVED when the totals actually agree.
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.documents import (
    GoodsReceiptDocument,
    GoodsReceiptLine,
    normalise_item,
    to_decimal,
)
from app.models.goods_receipt import (
    GoodsReceipt,
    GoodsReceiptItem,
    GoodsReceiptSerial,
)


logger = logging.getLogger(__name__)


class GoodsReceiptRepository:

    def __init__(self, db: Session):
        self.db = db

    # ── Reads ────────────────────────────────────────────────────────

    def get_row(self, grn_number: str) -> GoodsReceipt | None:
        return self.db.execute(
            select(GoodsReceipt)
            .where(GoodsReceipt.grn_number == grn_number)
            .limit(1)
        ).scalars().first()

    def get_by_number(
        self,
        grn_number: str,
    ) -> GoodsReceiptDocument | None:
        row = self.get_row(grn_number)

        return None if row is None else self.to_document(row)

    def get_by_task(self, task_id: uuid.UUID) -> GoodsReceipt | None:
        """Used to keep GRN creation idempotent across task retries."""
        return self.db.execute(
            select(GoodsReceipt)
            .where(GoodsReceipt.task_id == task_id)
            .limit(1)
        ).scalars().first()

    def latest_for_po(
        self,
        po_number: str,
    ) -> GoodsReceiptDocument | None:
        """Most recent goods receipt against a PO, if any."""
        row = self.db.execute(
            select(GoodsReceipt)
            .where(GoodsReceipt.po_number == po_number)
            .order_by(GoodsReceipt.created_at.desc())
            .limit(1)
        ).scalars().first()

        return None if row is None else self.to_document(row)

    def count_for_po(self, po_number: str) -> int:
        return self.db.execute(
            select(func.count(GoodsReceipt.id))
            .where(GoodsReceipt.po_number == po_number)
        ).scalar() or 0

    def cumulative_received(self, po_number: str) -> dict[str, Decimal]:
        """Total received per item across every GRN for this PO.

        Keyed by the normalised item name so a casing difference between
        two delivery notes does not split one item into two.
        """
        rows = self.db.execute(
            select(
                GoodsReceiptItem.item_name,
                GoodsReceiptItem.received_quantity,
            )
            .join(
                GoodsReceipt,
                GoodsReceipt.id == GoodsReceiptItem.goods_receipt_id,
            )
            .where(GoodsReceipt.po_number == po_number)
        ).all()

        totals: dict[str, Decimal] = {}

        for item_name, received in rows:
            key = normalise_item(item_name)
            totals[key] = totals.get(key, Decimal("0")) + to_decimal(
                received
            )

        return totals

    def existing_serials(self, serials: list[str]) -> set[str]:
        """Which of these serial numbers have already been received.

        S1 raises a DUPLICATE_SERIAL discrepancy for these rather than
        rejecting the delivery: a unit returned and re-received under an
        RMA carries the same serial, and the receipt still needs
        recording. The problem becomes visible, not fatal.
        """
        if not serials:
            return set()

        rows = self.db.execute(
            select(GoodsReceiptSerial.serial_number)
            .where(GoodsReceiptSerial.serial_number.in_(serials))
        ).scalars().all()

        return set(rows)

    def to_document(self, row: GoodsReceipt) -> GoodsReceiptDocument:
        return GoodsReceiptDocument(
            grn_number=row.grn_number,
            po_number=row.po_number,
            status=row.status,
            supplier=row.supplier,
            delivery_date=row.delivery_date,
            receiving_person=row.receiving_person,
            items=[
                GoodsReceiptLine(
                    item=item.item_name,
                    ordered_quantity=item.ordered_quantity,
                    received_quantity=item.received_quantity,
                    unit_price=item.unit_price,
                    total_amount=item.total_amount,
                    condition=item.condition,
                    qty_status=item.qty_status,
                    serial_numbers=[
                        serial.serial_number
                        for serial in item.serials
                    ],
                )
                for item in row.items
            ],
            discrepancies=row.payload.get("discrepancies", []),
            total_items_expected=to_decimal(
                row.payload.get("total_items_expected")
            ),
            total_items_received=to_decimal(
                row.payload.get("total_items_received")
            ),
        )

    # ── Writes ───────────────────────────────────────────────────────

    def create(
        self,
        task_id: uuid.UUID,
        correlation_id: uuid.UUID,
        grn_number: str,
        purchase_order_id: uuid.UUID,
        po_number: str,
        status: str,
        items: list[dict],
        supplier: str | None = None,
        delivery_date: str | None = None,
        receiving_person: str | None = None,
        payload: dict | None = None,
    ) -> GoodsReceipt:
        """Persist a GRN, its lines and every serial captured.

        No commit — the caller owns the transaction.
        """
        now = datetime.now(timezone.utc)

        receipt = GoodsReceipt(
            id=uuid.uuid4(),
            task_id=task_id,
            correlation_id=correlation_id,
            grn_number=grn_number,
            purchase_order_id=purchase_order_id,
            po_number=po_number,
            supplier=supplier,
            status=status,
            delivery_date=delivery_date,
            receiving_person=receiving_person,
            payload=payload or {},
        )

        for item in items:
            received = to_decimal(item.get("received_quantity"))
            unit_price = to_decimal(item.get("unit_price"))

            line = GoodsReceiptItem(
                id=uuid.uuid4(),
                item_name=item.get("item", ""),
                ordered_quantity=to_decimal(
                    item.get("ordered_quantity")
                ),
                received_quantity=received,
                unit_price=unit_price,
                total_amount=received * unit_price,
                condition=item.get("condition", "GOOD"),
                qty_status=item.get("qty_status", "MATCH"),
            )

            for serial_number in item.get("serial_numbers", []):
                line.serials.append(
                    GoodsReceiptSerial(
                        id=uuid.uuid4(),
                        item_name=line.item_name,
                        serial_number=str(serial_number),
                        received_at=now,
                    )
                )

            receipt.items.append(line)

        self.db.add(receipt)

        return receipt
