"""Backward compatibility — import from the new gateway location."""
from app.gateways.procurement_base import ProcurementGateway, PurchaseOrderResult  # noqa: F401
from app.gateways.mock_procurement import MockProcurementGateway  # noqa: F401