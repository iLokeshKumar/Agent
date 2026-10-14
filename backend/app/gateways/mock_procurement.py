import logging
from decimal import Decimal

from app.gateways.procurement_base import (
    ProcurementGateway,
    PurchaseOrderResult,
)

logger = logging.getLogger(__name__)


class MockProcurementGateway(ProcurementGateway):
    """Mock procurement gateway for local development.

    Simulates creating purchase orders without external calls.
    Replace with ZohoProcurementGateway or MCPProcurementGateway
    when ready to go live.
    """

    def __init__(self):
        self._store: dict[str, PurchaseOrderResult] = {}

    def create_purchase_order(
        self,
        *,
        po_number: str,
        vendor: str,
        items: list[dict],
        total: Decimal,
        requested_by: str | None = None,
    ) -> PurchaseOrderResult:

        result = PurchaseOrderResult(
            purchase_id=po_number,
            vendor=vendor,
            total=total,
            status="CREATED",
            items=items,
        )

        self._store[po_number] = result

        logger.info(
            "MockGateway: created PO %s — vendor=%s total=%s",
            po_number, vendor, total,
        )

        return result

    def get_purchase_order(
        self,
        purchase_id: str,
    ) -> PurchaseOrderResult | None:
        return self._store.get(purchase_id)
