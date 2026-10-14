"""P4 — Invoice Matching.

A real three-way match: the invoice against the **stored** purchase
order and the **stored** goods receipt. Previously the PO amount fell
back to the invoice amount when the payload omitted it, so the match
compared the invoice against itself and always agreed. Now both sides
come from the database and a disagreement is a disagreement.

No goods receipt means no match. An invoice cannot be paid before the
goods arrive, so a PO with no GRN is refused rather than half-matched.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.agents.base import BaseAgent, AgentResult
from app.contracts.documents import normalise_item, to_decimal
from app.models.task import Task
from app.repository.goods_receipt_repository import (
    GoodsReceiptRepository,
)
from app.repository.invoice_repository import InvoiceRepository
from app.repository.purchase_order_repository import (
    PurchaseOrderRepository,
)
from app.services.documents import document_session


# Money agrees to the paisa. Anything finer is float noise, not a
# billing dispute.
TOLERANCE = Decimal("0.01")


class P4InvoiceMatching(BaseAgent):
    """Invoice Matching Agent — P4

    Performs three-way matching:
    - Invoice vs Purchase Order (amount, item quantities and prices)
    - Invoice vs GRN (cumulative received quantities)
    - Full match completes; anything else routes to the approval gate
    """

    agent_code = "P4"
    agent_name = "Invoice Matching"

    def __init__(self):
        super().__init__()

    def describe(self) -> str:
        return (
            "Three-way invoice matching: validates the invoice against "
            "the stored purchase order and goods receipt. Routes "
            "mismatches to the approval gate."
        )

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        if not self._invoice_number(payload):
            return "'invoice_number' is required"

        if not payload.get("po_number"):
            return "'po_number' is required"

        # 'invoice_amount' is deliberately not checked here: if the
        # invoice is already registered, its amount comes from the
        # document. That check needs the database, so it lives in
        # execute().
        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        invoice_number = self._invoice_number(payload)
        po_number = payload["po_number"]

        with document_session() as db:
            purchase_order = PurchaseOrderRepository(db).get_by_number(
                po_number
            )

            if purchase_order is None:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error=(
                        f"PO {po_number} not found — nothing to match "
                        "this invoice against"
                    ),
                )

            receipts = GoodsReceiptRepository(db)

            goods_receipt, receipt_error = self._goods_receipt(
                receipts, payload, po_number
            )

            if receipt_error is not None:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error=receipt_error,
                )

            received = receipts.cumulative_received(po_number)

            invoice, error = self._invoice(
                db, payload, invoice_number, purchase_order, task_id
            )

            if error is not None:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error=error,
                )

        # ── Leg 0: vendor agrees ─────────────────────────────────
        # An invoice from someone else against our PO is exactly the
        # fraud-shaped error this phase exists to catch. Compared on
        # the stored documents, case-insensitively.
        vendor_match = self._vendors_agree(
            invoice.vendor, purchase_order.vendor
        )

        # ── Leg 1: invoice vs PO total ───────────────────────────────
        variance = invoice.invoice_amount - purchase_order.total_amount
        amount_match = abs(variance) < TOLERANCE

        # ── Leg 2: invoice lines vs PO lines ─────────────────────────
        item_matches = [
            self._match_line(line, purchase_order)
            for line in invoice.items
        ]

        if item_matches:
            items_match = all(
                match["status"] == "MATCH" for match in item_matches
            )
            item_basis = "line_level"
        else:
            # A single-total invoice with no line detail. The amount and
            # the goods receipt are then the whole of the evidence —
            # said plainly rather than reported as a line match.
            items_match = True
            item_basis = "amount_only"

        # ── Leg 3: invoice lines vs goods actually received ──────────
        grn_matches = [
            self._match_receipt(line, received) for line in invoice.items
        ]

        # Under-billing is not a payment risk; being billed for more
        # than arrived is exactly what this leg exists to catch.
        grn_match = not any(
            match["status"] == "OVER_BILLED" for match in grn_matches
        )

        match_status = self._match_status(
            vendor_match, amount_match, items_match, grn_match
        )

        match_result = {
            "invoice_number": invoice.invoice_number,
            "po_number": po_number,
            "grn_number": goods_receipt.grn_number,
            "match_status": match_status,
            "vendor_match": vendor_match,
            "po_vendor": purchase_order.vendor,
            "invoice_vendor": invoice.vendor,
            "invoice_amount": float(invoice.invoice_amount),
            "po_amount": float(purchase_order.total_amount),
            "amount_variance": float(variance.quantize(TOLERANCE)),
            "po_amount_match": amount_match,
            "item_matches": item_matches,
            "item_match_basis": item_basis,
            "all_items_match": items_match,
            "grn_matches": grn_matches,
            "grn_match": grn_match,
            "matched_at": now.isoformat(),
        }

        actions = [
            f"loaded_po:{po_number}",
            f"loaded_grn:{goods_receipt.grn_number}",
            "matched_against_po",
            "matched_against_grn",
        ]

        summary = {
            "status": match_status,
            "amount_variance": match_result["amount_variance"],
            "items_matched": sum(
                1 for m in item_matches if m["status"] == "MATCH"
            ),
            "items_mismatched": sum(
                1 for m in item_matches if m["status"] != "MATCH"
            ),
        }

        if match_status == "FULLY_MATCHED":
            self.logger.info(
                "Invoice %s vs PO %s: FULLY_MATCHED",
                invoice.invoice_number,
                po_number,
            )

            return AgentResult(
                status="SUCCESS",
                output={
                    "match_result": match_result,
                    "summary": summary,
                },
                actions_taken=actions + ["approved_for_payment"],
            )

        # ── Exception path ───────────────────────────────────────────
        reason = self._mismatch_reason(
            match_result,
            vendor_match,
            amount_match,
            items_match,
            grn_match,
        )

        if self._approval_granted(task_id):
            # A human has looked at the exception and accepted it. The
            # match still reads MISMATCH — the facts have not changed —
            # but the workflow may proceed to the books.
            match_result["approved_despite_mismatch"] = True

            self.logger.info(
                "Invoice %s: %s accepted by approval — proceeding",
                invoice.invoice_number,
                match_status,
            )

            return AgentResult(
                status="SUCCESS",
                output={
                    "match_result": match_result,
                    "summary": summary,
                    "action_required": None,
                    "warning": f"{reason} Approved by a human.",
                },
                actions_taken=actions + ["mismatch_approved"],
            )

        self.logger.warning(
            "Invoice %s vs PO %s: %s — requesting approval",
            invoice.invoice_number,
            po_number,
            match_status,
        )

        return AgentResult(
            status="NEEDS_APPROVAL",
            output={
                "approval_type": "INVOICE_MISMATCH",
                "approval_payload": {
                    "invoice_number": invoice.invoice_number,
                    "po_number": po_number,
                    "grn_number": goods_receipt.grn_number,
                    "match_status": match_status,
                    "vendor_match": vendor_match,
                    "invoice_amount": float(invoice.invoice_amount),
                    "po_amount": float(purchase_order.total_amount),
                    "amount_variance": match_result["amount_variance"],
                    "vendor": purchase_order.vendor,
                },
                "reason": reason,
                "match_result": match_result,
                "summary": summary,
                "action_required": (
                    "Invoice does not match the purchase order. "
                    "Manual review required before payment."
                ),
            },
            actions_taken=actions + ["flagged_for_review"],
        )

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _invoice_number(payload: dict) -> str | None:
        """The invoice's own number, under either spelling."""
        return payload.get("invoice_number") or payload.get("invoice_id")

    @staticmethod
    def _goods_receipt(
        receipts: GoodsReceiptRepository,
        payload: dict,
        po_number: str,
    ):
        """The GRN named in the payload, else the latest for the PO.

        Returns ``(document, error)``. A receipt must be on file either
        way: the goods arriving is what makes an invoice payable, so a PO
        with no deliveries and a payload naming a receipt that does not
        exist are refused by the same rule.
        """
        grn_number = payload.get("grn_number")

        if grn_number:
            document = receipts.get_by_number(grn_number)
        else:
            document = receipts.latest_for_po(po_number)

        if document is not None:
            return document, None

        subject = (
            f"GRN {grn_number} is not on file"
            if grn_number
            else f"PO {po_number} has no goods receipt"
        )

        return None, (
            f"{subject} — an invoice cannot be matched before the "
            "goods arrive"
        )

    def _invoice(
        self,
        db,
        payload: dict,
        invoice_number: str,
        purchase_order,
        task_id: uuid.UUID | None,
    ):
        """The invoice document, registering it first if it is new.

        Returns ``(document, error)``. An invoice already on file is used
        as stored — the payload cannot talk the platform into a different
        amount than the one it recorded when the invoice arrived.
        """
        repository = InvoiceRepository(db)

        existing = repository.get_by_number(invoice_number)

        if existing is not None:
            return existing, None

        amount = payload.get("invoice_amount")

        if amount is None or to_decimal(amount) <= 0:
            return None, (
                f"Invoice {invoice_number} is not on file, so "
                "'invoice_amount' is required to register it"
            )

        if task_id is None:
            return None, (
                f"Invoice {invoice_number} is not on file and cannot be "
                "registered outside a task"
            )

        task = db.get(Task, task_id)

        if task is None:
            return None, (
                f"Task {task_id} not found — cannot register invoice "
                f"{invoice_number}"
            )

        repository.create(
            task_id=task_id,
            correlation_id=task.correlation_id,
            invoice_number=invoice_number,
            po_number=purchase_order.po_number,
            purchase_order_id=(
                PurchaseOrderRepository(db)
                .get_row(purchase_order.po_number)
                .id
            ),
            invoice_amount=to_decimal(amount),
            vendor=payload.get("vendor") or purchase_order.vendor,
            currency=payload.get("currency", purchase_order.currency),
            invoice_date=payload.get("invoice_date"),
            items=(
                payload.get("invoice_items")
                or payload.get("items")
                or []
            ),
            payload={"source": "invoice_matching_task"},
        )

        db.commit()

        self.logger.info(
            "Registered invoice %s against PO %s",
            invoice_number,
            purchase_order.po_number,
        )

        return repository.get_by_number(invoice_number), None

    @staticmethod
    def _match_line(line, purchase_order) -> dict:
        """One invoice line against its purchase order line."""
        ordered = purchase_order.line_for(line.item)

        if ordered is None:
            return {
                "item": line.item,
                "inv_qty": float(line.quantity),
                "inv_price": float(line.unit_price),
                "status": "NO_PO_LINE",
            }

        qty_match = line.quantity == ordered.quantity
        price_match = abs(line.unit_price - ordered.unit_price) < TOLERANCE

        return {
            "item": line.item,
            "po_qty": float(ordered.quantity),
            "inv_qty": float(line.quantity),
            "qty_match": qty_match,
            "po_price": float(ordered.unit_price),
            "inv_price": float(line.unit_price),
            "price_match": price_match,
            "status": (
                "MATCH" if qty_match and price_match else "MISMATCH"
            ),
        }

    @staticmethod
    def _match_receipt(line, received: dict[str, Decimal]) -> dict:
        """One invoice line against what actually arrived."""
        arrived = received.get(
            normalise_item(line.item), Decimal("0")
        )

        if line.quantity > arrived:
            status = "OVER_BILLED"
        elif line.quantity < arrived:
            status = "UNDER_BILLED"
        else:
            status = "MATCH"

        return {
            "item": line.item,
            "inv_qty": float(line.quantity),
            "received_qty": float(arrived),
            "status": status,
        }

    @staticmethod
    def _vendors_agree(invoice_vendor, po_vendor) -> bool:
        """Vendor leg: same supplier on both documents.

        Only enforced when both sides actually name one — an old row
        with no vendor recorded cannot be convicted of a disagreement.
        """
        if not invoice_vendor or not po_vendor:
            return True

        return (
            str(invoice_vendor).strip().lower()
            == str(po_vendor).strip().lower()
        )

    @staticmethod
    def _match_status(
        vendor_match: bool,
        amount_match: bool,
        items_match: bool,
        grn_match: bool,
    ) -> str:
        checks = (vendor_match, amount_match, items_match, grn_match)

        if all(checks):
            return "FULLY_MATCHED"

        if any(checks):
            return "PARTIALLY_MATCHED"

        return "MISMATCH"

    def _mismatch_reason(
        self,
        match_result: dict,
        vendor_match: bool,
        amount_match: bool,
        items_match: bool,
        grn_match: bool,
    ) -> str:
        """Why this invoice was not paid, in one sentence."""
        problems = []

        if not vendor_match:
            problems.append(
                f"invoice vendor '{match_result['invoice_vendor']}' "
                f"is not PO vendor "
                f"'{match_result['po_vendor']}'"
            )

        if not amount_match:
            problems.append(
                f"invoice {match_result['invoice_amount']} does not "
                f"match PO {match_result['po_amount']} "
                f"(variance {match_result['amount_variance']})"
            )

        if not items_match:
            mismatched = [
                match["item"]
                for match in match_result["item_matches"]
                if match["status"] != "MATCH"
            ]
            problems.append(
                "line disagreement on " + ", ".join(mismatched)
            )

        if not grn_match:
            over = [
                match["item"]
                for match in match_result["grn_matches"]
                if match["status"] == "OVER_BILLED"
            ]
            problems.append(
                "billed for more than was received: " + ", ".join(over)
            )

        return (
            f"{match_result['match_status']}: "
            + "; ".join(problems)
            + "."
        )
