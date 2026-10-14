from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PurchaseOrderResult:
    """Standard result from any procurement gateway."""
    purchase_id: str
    vendor: str | None
    total: Decimal
    status: str  # CREATED | SUBMITTED | FAILED
    items: list[dict]
    error: str | None = None


class ProcurementGateway(ABC):
    """Interface for procurement system adapters.

    Implementations:
        - MockProcurementGateway   ← local mock (NOW)
        - ZohoProcurementGateway   ← Zoho integration (NEXT)
        - MCPProcurementGateway    ← MCP vendor API (LATER)
    """

    @abstractmethod
    def create_purchase_order(
        self,
        *,
        po_number: str,
        vendor: str,
        items: list[dict],
        total: Decimal,
        requested_by: str | None = None,
    ) -> PurchaseOrderResult:
        """Create a purchase order in the procurement system."""
        ...

    @abstractmethod
    def get_purchase_order(
        self,
        purchase_id: str,
    ) -> PurchaseOrderResult | None:
        """Retrieve an existing purchase order by ID."""
        ...
