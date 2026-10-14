"""Purchase request persistence.

Persisted at intake — the moment ``POST /tasks`` accepts a
``purchase_request`` — so the request is a real document before any
policy is evaluated. The PO raised from it later shares the same
``correlation_id``, which is how the two are joined; nothing writes
back into this table except the status advance to CONVERTED.
"""

import logging
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.documents import to_decimal
from app.models.purchase_request import (
    PurchaseRequest,
    PurchaseRequestItem,
)


logger = logging.getLogger(__name__)


class PurchaseRequestRepository:

    def __init__(self, db: Session):
        self.db = db

    # ── Reads ────────────────────────────────────────────────────────

    def get_row(self, request_id: uuid.UUID) -> PurchaseRequest | None:
        return self.db.get(PurchaseRequest, request_id)

    def get_by_task(self, task_id: uuid.UUID) -> PurchaseRequest | None:
        return self.db.execute(
            select(PurchaseRequest)
            .where(PurchaseRequest.task_id == task_id)
            .limit(1)
        ).scalars().first()

    def get_by_correlation(
        self,
        correlation_id: uuid.UUID,
    ) -> PurchaseRequest | None:
        return self.db.execute(
            select(PurchaseRequest)
            .where(PurchaseRequest.correlation_id == correlation_id)
            .limit(1)
        ).scalars().first()

    # ── Writes ───────────────────────────────────────────────────────

    def create(
        self,
        *,
        task_id: uuid.UUID,
        correlation_id: uuid.UUID,
        total_amount: Decimal,
        requested_by: str | None = None,
        vendor_id: uuid.UUID | None = None,
        vendor_name: str | None = None,
        budget_limit: Decimal | None = None,
        currency: str = "INR",
        notes: str | None = None,
        items: list[dict] | None = None,
        payload: dict | None = None,
        status: str = "SUBMITTED",
    ) -> PurchaseRequest:
        """Persist the request and its lines.

        No flush/commit here — the caller owns the transaction.
        """
        purchase_request = PurchaseRequest(
            id=uuid.uuid4(),
            task_id=task_id,
            correlation_id=correlation_id,
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            requested_by=requested_by,
            budget_limit=to_decimal(budget_limit) if budget_limit else None,
            total_amount=to_decimal(total_amount),
            currency=currency,
            status=status,
            notes=notes,
            payload=payload or {},
        )

        for item in items or []:
            quantity = to_decimal(item.get("quantity"))
            unit_price = to_decimal(item.get("unit_price"))

            purchase_request.items.append(
                PurchaseRequestItem(
                    id=uuid.uuid4(),
                    item_name=item.get("item", ""),
                    quantity=quantity,
                    unit_price=unit_price,
                    total_amount=to_decimal(
                        item.get("total_amount")
                    ) or quantity * unit_price,
                )
            )

        self.db.add(purchase_request)

        return purchase_request

    def set_status(self, task_id: uuid.UUID, status: str) -> str | None:
        """Advance the request's workflow state by its originating task."""
        row = self.get_by_task(task_id)

        if row is None:
            logger.warning(
                "Cannot set status on unknown purchase request "
                "for task %s",
                task_id,
            )
            return None

        if row.status == status:
            return status

        logger.info(
            "Purchase request %s status %s -> %s",
            row.id,
            row.status,
            status,
        )

        row.status = status

        return status
