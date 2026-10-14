"""S1 — GRN & Serialisation.

Receives goods **against a stored purchase order**. The ordered quantity
and unit price come from the PO document, not from the task payload, so a
receipt cannot quietly disagree with what was ordered — and a PO that was
never raised is refused outright rather than receipted.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.agents.base import BaseAgent, AgentResult
from app.contracts.documents import normalise_item, to_decimal
from app.repository.goods_receipt_repository import (
    GoodsReceiptRepository,
)
from app.repository.purchase_order_repository import (
    PurchaseOrderRepository,
)
from app.services.documents import document_session
from app.services.numbering import GRN_SEQUENCE, next_document_number


# Discrepancies that mean "look at this receipt by hand", as opposed to a
# plain quantity difference which is just a partial or excess delivery.
_NEEDS_ATTENTION = {
    "SERIAL_COUNT_MISMATCH",
    "DUPLICATE_SERIAL",
    "NOT_ON_PO",
}


def _number(value: Decimal) -> float | int:
    """JSON-friendly quantity: 2 rather than 2.0, 2.5 stays 2.5."""
    return int(value) if value == value.to_integral_value() else float(
        value
    )


class S1GRNSerialisation(BaseAgent):
    """GRN & Serialisation Agent — S1

    Handles goods receipt:
    - Loads the referenced purchase order, refusing an unknown one
    - Derives what is still outstanding across earlier deliveries
    - Captures serial numbers and flags ones already on file
    - Creates the GRN record and flags quantity discrepancies
    """

    agent_code = "S1"
    agent_name = "GRN and Serialisation"

    def __init__(self):
        super().__init__()

    def describe(self) -> str:
        return (
            "Goods receipt and serial number capture against a "
            "purchase order. Creates GRN records, captures serials "
            "and flags quantity discrepancies."
        )

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        # "items" is the canonical key; "received_items" is accepted
        # as an alias for clients that speak receiving-desk language.
        if not self._request_items(payload):
            return "Payload must contain 'items' as a non-empty list"

        items = self._request_items(payload)

        for i, item in enumerate(items):
            if not item.get("item"):
                return f"Item {i}: 'item' name is required"

            # Optional: an item with no quantity means "whatever is
            # still outstanding on the PO". Checked here only if given.
            for field in ("received_quantity", "quantity"):
                quantity = item.get(field)

                if quantity is not None and quantity < 0:
                    return f"Item {i}: '{field}' must be >= 0"

        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        po_number = payload.get("po_number")

        if not po_number:
            return AgentResult(
                status="VALIDATION_ERROR",
                error="'po_number' is required",
            )

        items = self._request_items(payload)

        with document_session() as db:
            purchase_order = PurchaseOrderRepository(db).get_by_number(
                po_number
            )

            if purchase_order is None:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error=(
                        f"PO {po_number} not found — goods cannot be "
                        "received against a purchase order that was "
                        "never raised"
                    ),
                )

            receipts = GoodsReceiptRepository(db)

            # Cumulative across every earlier delivery, so a second
            # instalment is measured against what is still outstanding.
            already_received = receipts.cumulative_received(po_number)

            duplicate_serials = receipts.existing_serials(
                self._all_serials(items)
            )

            # ── Gate: the delivery must agree with the PO ────────────
            #
            # The PO says 2, the truck unloads 5 — that is refused,
            # not receipted with a flag. Measured cumulatively across
            # earlier GRNs, so only a delivery that pushes the total
            # past the order trips it. A carton that was never on the
            # order is refused too — inventory cannot grow from data
            # no purchase ever asked for. Both pass only when the
            # caller sets ``allow_over_receipt: true``, the explicit
            # business rule allowing them, and even then the receipt
            # lands flagged so the exception stays visible.
            overages = self._overage_lines(
                purchase_order, items, already_received
            )

        if overages and not payload.get("allow_over_receipt"):
            described = "; ".join(
                f"{o['item']} received {o['received']} but only "
                f"{o['outstanding']} outstanding (ordered "
                f"{o['ordered']})"
                if o["type"] == "OVER"
                else f"{o['item']} is not on PO {po_number}"
                for o in overages
            )

            return AgentResult(
                status="VALIDATION_ERROR",
                error=(
                    f"GRN refused — delivery disagrees with "
                    f"PO {po_number}: {described}. Set "
                    f"allow_over_receipt=true to accept anyway; "
                    "the receipt will be flagged OVER_RECEIVED."
                ),
            )

        grn_number = next_document_number("GRN", GRN_SEQUENCE, now)

        delivery_date = payload.get(
            "delivery_date", now.strftime("%Y-%m-%d")
        )
        supplier = payload.get(
            "supplier", purchase_order.vendor or "Unknown"
        )
        vehicle_number = payload.get("vehicle_number")
        receiving_person = payload.get(
            "receiving_person", requested_by
        )

        line_items = []
        discrepancies = []
        serial_numbers = []

        total_received = Decimal("0")
        total_expected = Decimal("0")

        seen_in_this_delivery: set[str] = set()

        for item in items:
            item_name = item["item"]

            ordered_line = purchase_order.line_for(item_name)

            if ordered_line is None:
                # Receive it, but say so. Refusing the whole delivery
                # because one carton is unexpected is the worse failure.
                ordered = Decimal("0")
                unit_price = to_decimal(item.get("unit_price"))

                discrepancies.append({
                    "item": item_name,
                    "type": "NOT_ON_PO",
                    "po_number": po_number,
                })
            else:
                ordered = ordered_line.quantity
                unit_price = ordered_line.unit_price

            prior = already_received.get(
                normalise_item(item_name), Decimal("0")
            )

            outstanding = max(ordered - prior, Decimal("0"))
            received = self._received_quantity(item, outstanding)

            total_expected += outstanding
            total_received += received

            qty_status = "MATCH"

            if received > outstanding:
                qty_status = "OVER_RECEIVED"
                discrepancies.append({
                    "item": item_name,
                    "type": "OVER",
                    "expected": _number(outstanding),
                    "received": _number(received),
                    "ordered": _number(ordered),
                    "previously_received": _number(prior),
                })
            elif received < outstanding:
                qty_status = "UNDER_RECEIVED"
                discrepancies.append({
                    "item": item_name,
                    "type": "UNDER",
                    "expected": _number(outstanding),
                    "received": _number(received),
                    "ordered": _number(ordered),
                    "previously_received": _number(prior),
                })

            serials = [
                str(serial)
                for serial in item.get("serial_numbers", [])
            ]

            serial_status = "OK"

            if serials and len(serials) != received:
                serial_status = (
                    f"MISMATCH: expected {_number(received)} serials, "
                    f"got {len(serials)}"
                )
                discrepancies.append({
                    "item": item_name,
                    "type": "SERIAL_COUNT_MISMATCH",
                    "expected_count": _number(received),
                    "actual_count": len(serials),
                })

            for serial in serials:
                if (
                    serial in duplicate_serials
                    or serial in seen_in_this_delivery
                ):
                    serial_status = f"DUPLICATE: {serial}"
                    discrepancies.append({
                        "item": item_name,
                        "type": "DUPLICATE_SERIAL",
                        "serial_number": serial,
                    })

                seen_in_this_delivery.add(serial)

                serial_numbers.append({
                    "item": item_name,
                    "serial_number": serial,
                    "received_at": now.isoformat(),
                })

            line_items.append({
                "item": item_name,
                # The PO's figure, not this delivery's outstanding
                # balance — that is what the field name means.
                "ordered_quantity": _number(ordered),
                "outstanding_quantity": _number(outstanding),
                "received_quantity": _number(received),
                "unit_price": float(unit_price),
                "line_total": float(
                    (received * unit_price).quantize(Decimal("0.01"))
                ),
                "condition": item.get("condition", "GOOD"),
                "serial_numbers": serials,
                "qty_status": qty_status,
                "serial_status": serial_status,
                "remarks": item.get("remarks", ""),
            })

        grn_status = self._grn_status(discrepancies)

        grn = {
            "grn_number": grn_number,
            "po_number": po_number,
            "status": grn_status,
            "supplier": supplier,
            "delivery_date": delivery_date,
            "vehicle_number": vehicle_number,
            "receiving_person": receiving_person,
            "items": line_items,
            "total_items_expected": _number(total_expected),
            "total_items_received": _number(total_received),
            "discrepancies": discrepancies,
            "total_serial_numbers": len(serial_numbers),
            "serial_numbers": serial_numbers,
            "created_at": now.isoformat(),
        }

        actions = [
            f"loaded_po:{po_number}",
            "scanned_items",
            f"created_grn:{grn_number}",
        ]

        if serial_numbers:
            actions.append(
                f"captured_serial_numbers:{len(serial_numbers)}"
            )

        if discrepancies:
            actions.append(
                f"flagged_discrepancies:{len(discrepancies)}"
            )

        output = {
            "grn_id": grn_number,
            "grn": grn,
            "summary": {
                "grn_number": grn_number,
                "po_number": po_number,
                "status": grn_status,
                "total_received": _number(total_received),
                "discrepancy_count": len(discrepancies),
                "serial_numbers_captured": len(serial_numbers),
            },
        }

        self.logger.info(
            "GRN %s created against PO %s — %s received, "
            "%d discrepancies",
            grn_number,
            po_number,
            _number(total_received),
            len(discrepancies),
        )

        return AgentResult(
            status="SUCCESS",
            output=output,
            actions_taken=actions,
        )

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _received_quantity(
        item: dict,
        outstanding: Decimal,
    ) -> Decimal:
        """How much of this line arrived.

        Precedence: ``received_quantity``, then ``quantity``, then
        whatever is still outstanding on the PO. An explicit number is a
        statement about this delivery whichever key it came under;
        omitting both means "the rest of the order".
        """
        for field in ("received_quantity", "quantity"):
            if item.get(field) is not None:
                return to_decimal(item[field])

        return outstanding

    @staticmethod
    def _request_items(payload: dict) -> list[dict]:
        """The delivery lines, under either supported key."""
        items = payload.get("items") or payload.get("received_items")

        return items if isinstance(items, list) else []

    @staticmethod
    def _overage_lines(
        purchase_order,
        items: list[dict],
        already_received: dict[str, Decimal],
    ) -> list[dict]:
        """Lines taking more than the PO owes, or never ordered at all."""
        problems: list[dict] = []

        for item in items:
            item_name = item["item"]
            ordered_line = purchase_order.line_for(item_name)

            if ordered_line is None:
                problems.append({
                    "item": item_name,
                    "type": "NOT_ON_PO",
                })
                continue

            prior = already_received.get(
                normalise_item(item_name), Decimal("0")
            )
            outstanding = max(
                ordered_line.quantity - prior, Decimal("0")
            )
            received = S1GRNSerialisation._received_quantity(
                item, outstanding
            )

            if received > outstanding:
                problems.append({
                    "item": item_name,
                    "type": "OVER",
                    "ordered": _number(ordered_line.quantity),
                    "outstanding": _number(outstanding),
                    "received": _number(received),
                })

        return problems

    @staticmethod
    def _all_serials(items: list[dict]) -> list[str]:
        return [
            str(serial)
            for item in items
            for serial in item.get("serial_numbers", [])
        ]

    @staticmethod
    def _grn_status(discrepancies: list[dict]) -> str:
        """COMPLETE | PARTIAL | OVER_RECEIVED | HAS_DISCREPANCIES."""
        if not discrepancies:
            return "COMPLETE"

        types = {d.get("type") for d in discrepancies}

        if types & _NEEDS_ATTENTION:
            return "HAS_DISCREPANCIES"

        # Quantity-only difference. An excess delivery is not a partial
        # one — reporting PARTIAL for it hid over-receipts entirely.
        if "OVER" in types:
            return "OVER_RECEIVED"

        return "PARTIAL"
