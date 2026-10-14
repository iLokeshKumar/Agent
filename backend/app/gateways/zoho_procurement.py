"""Real procurement gateway — writes PO to LIVE Zoho Books (no mock).

Real-data contract:
- Requires Zoho Books configured (CLIENT_ID/SECRET/REFRESH_TOKEN/ORG_ID)
- Resolves vendor name → vendor_id via Zoho contacts (type=vendor)
- Creates PO in Zoho Books via POST /purchaseorders
- Also returns local PO number for ledger linkage; the post-completion
  hook in workflow_engine still writes the local purchase_orders row
  (single-write: Zoho is master, local is mirror + approval state)
- If Zoho not configured or vendor not found, raises — never silently
  falls back to Mock.
"""

import logging
from decimal import Decimal

from app.core.config import settings
from app.gateways.procurement_base import ProcurementGateway, PurchaseOrderResult
from app.integrations.zoho_books import get_zoho_client, ZohoBooksError

logger = logging.getLogger(__name__)


class ZohoProcurementGateway(ProcurementGateway):
    def create_purchase_order(self, *, po_number, vendor, items, total, requested_by=None):
        client = get_zoho_client()
        if not client.configured:
            raise ZohoBooksError(0, "Zoho Books not configured — cannot create PO. Run GET /auth/zoho")

        # Resolve vendor → vendor_id via live search (real Zoho data)
        vendor_id = self._resolve_vendor_id(client, vendor)

        # Build Zoho PO payload with live item data
        # Zoho requires line_items with item_id or name+rate+quantity
        line_items = []
        for it in items:
            # Try to resolve item_id live; fallback to name/rate
            item_id, item_tax_id = self._resolve_item(client, it.get("item"))
            entry = {
                "quantity": float(it.get("quantity", 1)),
                "rate": float(it.get("unit_price", 0)),
                "description": it.get("item", ""),
            }
            if item_id:
                entry["item_id"] = item_id
            else:
                entry["name"] = it.get("item", "")
            # India GST: Zoho requires tax_id on every PO line item
            # Use item's own tax; fall back to IGST18 for inter-state vendor
            tax_id = it.get("tax_id") or item_tax_id or settings.zoho_default_po_tax_id
            entry["tax_id"] = tax_id
            line_items.append(entry)

        payload = {
            "vendor_id": vendor_id,
            "reference_number": po_number,
            "line_items": line_items,
            "notes": f"Created by Yexis Agent P1 (requested_by={requested_by})",
        }

        try:
            # POST to Zoho Books — real network call
            po = client.create_purchase_order(payload)  # may need to implement in client
            zoho_po_id = po.get("purchaseorder_id") or po.get("purchaseorder_number") or po_number
            logger.info("Zoho PO created %s → %s total=%s", po_number, zoho_po_id, total)
            return PurchaseOrderResult(
                purchase_id=str(zoho_po_id),
                vendor=vendor,
                total=total,
                status="CREATED",
                items=items,
            )
        except AttributeError:
            # client.create_purchase_order not yet implemented — surface clearly
            logger.warning("Zoho client has no create_purchase_order method — PO mirrored locally only")
            # Still succeed with local number but flag that Zoho method missing
            raise ZohoBooksError(0, "Zoho client missing create_purchase_order — implement in zoho_books.py POST /purchaseorders")

    def get_purchase_order(self, purchase_id):
        client = get_zoho_client()
        try:
            data = client.get_purchase_order(purchase_id)
            return PurchaseOrderResult(
                purchase_id=purchase_id,
                vendor=data.get("vendor_name"),
                total=Decimal(str(data.get("total", 0))),
                status="CREATED",
                items=data.get("line_items", []),
            )
        except Exception:
            return None

    def _resolve_vendor_id(self, client, vendor_name: str) -> str:
        # Live search via Zoho Contacts API: search by vendor_name
        try:
            vendors = client.search_contacts(vendor_name, contact_type="vendor")
            if vendors:
                return vendors[0].get("contact_id") or vendors[0].get("contact_id")
        except Exception as exc:
            logger.warning("Vendor resolve failed for %r: %s", vendor_name, exc)
        # If not found, try list and exact match, else raise — never invent
        raise ZohoBooksError(404, f"Vendor '{vendor_name}' not found in Zoho Books contacts. Create vendor first or fix spelling (live Zoho data).")

    def _resolve_item(self, client, item_name: str) -> tuple[str | None, str | None]:
        """Return (item_id, tax_id) for the named item; both None if not found."""
        if not item_name:
            return None, None
        try:
            items = client.search_items(item_name)
            if items:
                hit = items[0]
                return hit.get("item_id"), hit.get("tax_id") or None
        except Exception:
            pass
        return None, None
