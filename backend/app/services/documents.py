"""Turn a successful agent run into a persisted business document.

Called from the executor once a task reaches COMPLETED. Each agent
publishes exactly one document — P1 a purchase order, S1 a goods
receipt, P4 a match outcome, F3 a posting — and the next agent in the
chain reads that document rather than the previous agent's result JSON.

Every recorder is idempotent. A task that somehow runs twice must not
produce two purchase orders, and the UNIQUE constraints on
``purchase_number`` / ``grn_number`` / ``invoice_number`` are the
backstop for that, not the mechanism.

Nothing here commits. The executor owns the transaction so the document
and the next queued task land together or not at all.
"""

import logging
from contextlib import contextmanager
from decimal import Decimal

from pydantic import ValidationError

from app.contracts.documents import (
    PurchaseOrderDocument,
    PurchaseOrderLine,
    SupplierInvoiceInput,
    normalise_item,
    to_decimal,
)
from app.core.db import SessionLocal
from app.models.purchase_order import PurchaseOrder
from app.models.task import Task
from app.repository.goods_receipt_repository import (
    GoodsReceiptRepository,
)
from app.repository.invoice_repository import InvoiceRepository
from app.repository.purchase_order_repository import (
    PurchaseOrderRepository,
)
from app.repository.purchase_request_repository import (
    PurchaseRequestRepository,
)


logger = logging.getLogger(__name__)


# PO states that a goods receipt is allowed to move. Once an invoice has
# been matched or posted, a late GRN must not walk the PO backwards.
_RECEIVABLE_PO_STATUSES = {
    "CREATED",
    "PARTIALLY_RECEIVED",
    "RECEIVED",
}


@contextmanager
def document_session():
    """Short-lived session for agents, which are handed no db handle.

    Matches what P1 already does for its budget check and PO number:
    open, use, close. Reading a document must not depend on the
    executor's transaction state.
    """
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def record_document(
    db,
    task: Task,
    agent_code: str,
    output: dict,
) -> None:
    """Persist the business document this agent just produced."""
    recorder = _RECORDERS.get(agent_code)

    if recorder is None:
        return

    recorder(db, task, output or {})


# ── P1: purchase order ───────────────────────────────────────────────


def _record_purchase_order(db, task: Task, output: dict) -> None:
    po_number = output.get("po_number")

    if not po_number:
        return

    repository = PurchaseOrderRepository(db)

    existing = repository.get_by_task(task.id)
    if existing is not None:
        logger.info(
            "Task %s already has PO %s — not writing a second",
            task.id,
            existing.purchase_number,
        )
        return

    if repository.get_row(po_number) is not None:
        # The sequence should make this impossible. If it happens, the
        # number was reused and writing anything would corrupt history.
        logger.error(
            "PO %s already exists for another task — refusing to "
            "record it again",
            po_number,
        )
        return

    document = _purchase_order_document(output, po_number)

    purchase_order = repository.create(
        task_id=task.id,
        correlation_id=task.correlation_id,
        po_number=po_number,
        vendor=document.vendor,
        items=[line.model_dump() for line in document.items],
        total_amount=document.total_amount,
        currency=document.currency,
        status="CREATED",
        payload=document.to_dict(),
    )

    # The PO row has to physically exist before anything points at it,
    # and this session runs with autoflush off. Two things point at it
    # immediately: an attached invoice carries a foreign key to
    # purchase_orders.id, and the chain re-reads the PO by number to
    # build the goods-receipt task. Without this flush the invoice insert
    # violates the key and the chain never starts.
    db.flush()

    logger.info(
        "Recorded purchase order %s (%s %s) for task %s",
        po_number,
        document.currency,
        document.total_amount,
        task.id,
    )

    _register_attached_invoice(db, task, purchase_order)

    # The request this task came from has now produced its document.
    # Same transaction, so the request can never read CONVERTED while
    # the PO that converted it failed to land.
    PurchaseRequestRepository(db).set_status(task.id, "CONVERTED")


def _purchase_order_document(
    output: dict,
    po_number: str,
) -> PurchaseOrderDocument:
    """Build the PO contract from P1's output."""
    lines = []

    for item in output.get("items", []):
        quantity = to_decimal(item.get("quantity"))
        unit_price = to_decimal(item.get("unit_price"))

        lines.append(
            PurchaseOrderLine(
                item=item.get("item", ""),
                quantity=quantity,
                unit_price=unit_price,
                total_amount=quantity * unit_price,
            )
        )

    return PurchaseOrderDocument(
        purchase_id=output.get("purchase_id") or po_number,
        po_number=po_number,
        vendor=output.get("vendor") or "",
        items=lines,
        total_amount=to_decimal(output.get("total_amount")),
        currency=output.get("currency") or "INR",
        status="CREATED",
    )


def _register_attached_invoice(
    db,
    task: Task,
    purchase_order: PurchaseOrder,
) -> None:
    """Register a ``supplier_invoice`` block attached to the request.

    The tax invoice usually travels with the goods, so a request may
    declare it up front. Absent, the chain simply stops after the goods
    receipt until the invoice is posted as its own task — it is the one
    number the platform must never invent.
    """
    block = (task.payload or {}).get("supplier_invoice")

    if not block:
        return

    try:
        parsed = SupplierInvoiceInput.model_validate(block)
    except ValidationError as exc:
        logger.warning(
            "Ignoring malformed supplier_invoice on task %s: %s",
            task.id,
            exc.errors(),
        )
        return

    repository = InvoiceRepository(db)

    if repository.get_row(parsed.invoice_number) is not None:
        logger.warning(
            "Invoice %s is already registered — not registering again",
            parsed.invoice_number,
        )
        return

    repository.create(
        task_id=task.id,
        correlation_id=task.correlation_id,
        invoice_number=parsed.invoice_number,
        po_number=purchase_order.purchase_number,
        purchase_order_id=purchase_order.id,
        invoice_amount=parsed.invoice_amount,
        vendor=purchase_order.vendor,
        currency=purchase_order.currency,
        invoice_date=parsed.invoice_date,
        items=parsed.items,
        payload={"source": "attached_to_purchase_request"},
    )

    logger.info(
        "Registered supplier invoice %s against PO %s",
        parsed.invoice_number,
        purchase_order.purchase_number,
    )


# ── S1: goods receipt ────────────────────────────────────────────────


def _record_goods_receipt(db, task: Task, output: dict) -> None:
    grn = output.get("grn") or {}
    grn_number = grn.get("grn_number")
    po_number = grn.get("po_number")

    if not grn_number or not po_number:
        return

    receipts = GoodsReceiptRepository(db)

    existing = receipts.get_by_task(task.id)
    if existing is not None:
        logger.info(
            "Task %s already has GRN %s — not writing a second",
            task.id,
            existing.grn_number,
        )
        return

    purchase_order = PurchaseOrderRepository(db).get_row(po_number)

    if purchase_order is None:
        # S1 refuses an unknown PO, so this means the PO was removed
        # between validation and here. Record nothing rather than a
        # receipt against a document that does not exist.
        logger.error(
            "Cannot record GRN %s — PO %s not found",
            grn_number,
            po_number,
        )
        return

    receipts.create(
        task_id=task.id,
        correlation_id=task.correlation_id,
        grn_number=grn_number,
        purchase_order_id=purchase_order.id,
        po_number=purchase_order.purchase_number,
        status=grn.get("status", "COMPLETE"),
        items=grn.get("items", []),
        supplier=grn.get("supplier"),
        delivery_date=grn.get("delivery_date"),
        receiving_person=grn.get("receiving_person"),
        payload=grn,
    )

    # The cumulative query below must see the rows just added, and this
    # session runs with autoflush off.
    db.flush()

    logger.info(
        "Recorded goods receipt %s against PO %s",
        grn_number,
        purchase_order.purchase_number,
    )

    refresh_po_status(db, purchase_order.purchase_number)


def refresh_po_status(db, po_number: str) -> str | None:
    """Derive the PO's status from what has actually been received.

    Cumulative across every GRN for the PO, so a part-delivered order
    reads PARTIALLY_RECEIVED and only reaches RECEIVED when the
    quantities genuinely agree. Deliberately not taken from the GRN's own
    status label, which reports OVER_RECEIVED and serial problems too.
    """
    purchase_orders = PurchaseOrderRepository(db)
    document = purchase_orders.get_by_number(po_number)

    if document is None:
        return None

    if document.status not in _RECEIVABLE_PO_STATUSES:
        # Already invoiced or posted; a late receipt does not undo that.
        return document.status

    received = GoodsReceiptRepository(db).cumulative_received(po_number)

    if not any(quantity > 0 for quantity in received.values()):
        return purchase_orders.set_status(po_number, "CREATED")

    fully_received = all(
        received.get(normalise_item(line.item), Decimal("0"))
        >= line.quantity
        for line in document.items
    )

    return purchase_orders.set_status(
        po_number,
        "RECEIVED" if fully_received else "PARTIALLY_RECEIVED",
    )


# ── P4: three-way match outcome ──────────────────────────────────────


def _record_invoice_match(db, task: Task, output: dict) -> None:
    match = output.get("match_result") or {}

    invoice_number = match.get("invoice_number")
    match_status = match.get("match_status")

    if not invoice_number or not match_status:
        return

    goods_receipt = None
    if match.get("grn_number"):
        goods_receipt = GoodsReceiptRepository(db).get_row(
            match["grn_number"]
        )

    InvoiceRepository(db).record_match(
        invoice_number=invoice_number,
        match_status=match_status,
        match_result=match,
        goods_receipt_id=(
            goods_receipt.id if goods_receipt is not None else None
        ),
    )

    if match_status == "FULLY_MATCHED" and match.get("po_number"):
        PurchaseOrderRepository(db).set_status(
            match["po_number"], "INVOICED"
        )


# ── F3: posted to books ──────────────────────────────────────────────


def _record_books_posting(db, task: Task, output: dict) -> None:
    """Close the loop: the invoice has reached the books.

    The document numbers come from the task payload, which the chain
    populated — F3 itself is period-based and knows nothing about a
    single purchase.

    The voucher lines are also staged for Tally here: STAGED rows in
    ``tally_staging_voucher``, awaiting accountant approval. Nothing
    posts to Tally from this path — approval and posting happen through
    the Tally Gateway, by a human hand.
    """
    payload = task.payload or {}

    invoice_number = payload.get("invoice_number")
    po_number = payload.get("po_number")

    if invoice_number:
        InvoiceRepository(db).mark_posted(invoice_number)

    if po_number:
        PurchaseOrderRepository(db).set_status(po_number, "POSTED")

    if invoice_number or po_number:
        logger.info(
            "Posted to books: invoice=%s po=%s",
            invoice_number,
            po_number,
        )

    # Stage for Tally — best-effort but loud on failure. A staging
    # problem must not roll back the Books-side posting above; the
    # vouchers can be re-staged idempotently later.
    entries = output.get("staged_entries") or []

    if entries:
        try:
            from app.integrations.tally_gateway import (
                TallyGateway,
                TallyGatewayError,
            )

            company = (output.get("summary") or {}).get(
                "company", "Default Company"
            )
            books_txn_id = (
                invoice_number
                or f"TASK-{task.id}"
            )

            staged = TallyGateway(db).stage_entries(
                entity="YEXIS-EL",
                books_txn_id=books_txn_id,
                entries=entries,
                correlation_id=task.correlation_id,
                company=company,
            )
            if staged:
                logger.info(
                    "Staged %d Tally voucher lines (%s/%s)",
                    len(staged), "YEXIS-EL", books_txn_id,
                )
        except Exception:
            logger.exception(
                "Tally staging failed for task %s — Books posting "
                "stands; re-stage via the gateway",
                task.id,
            )


# ── P2: RFQ document + quotes ────────────────────────────────────────


def _record_rfq(db, task: Task, output: dict) -> None:
    """Persist P2's documents, by mode.

    issue   → the RFQ row itself. Idempotent on task_id like the PO:
    a task that somehow runs twice must not send vendors a second RFQ.

    collect → one quote row per vendor in the output, appended with the
    repository's revision logic so a re-run appends nothing.

    compare → the buyer's award, when the output carries one. The RFQ
    then reads AWARDED, ready for P1.
    """
    from app.repository.rfq_repository import RfqRepository

    repository = RfqRepository(db)
    mode = output.get("mode")

    # ── issue ────────────────────────────────────────────────────────
    rfq_number = output.get("rfq_number")

    if not rfq_number:
        return

    if mode == "issue":
        existing = repository.get_by_task(task.id)

        if existing is not None:
            logger.info(
                "Task %s already has RFQ %s — not issuing a second",
                task.id,
                existing.rfq_number,
            )
            return

        if repository.get_row(rfq_number) is not None:
            # The sequence should make this impossible; if it happens
            # the number was reused and writing anything would
            # corrupt the vendors' quote threads.
            logger.error(
                "RFQ %s already exists for another task — refusing "
                "to record it again",
                rfq_number,
            )
            return

        repository.create(
            task_id=task.id,
            correlation_id=task.correlation_id,
            rfq_number=rfq_number,
            items=output.get("items") or [],
            vendor=", ".join(output.get("vendors") or []),
            status="ISSUED",
            payload={
                "vendors": output.get("vendors") or [],
                "items": output.get("items") or [],
                "currency": output.get("currency", "INR"),
                "need_by": output.get("need_by"),
            },
        )

        logger.info(
            "Recorded RFQ %s issued to %d vendor(s) for task %s",
            rfq_number,
            len(output.get("vendors") or []),
            task.id,
        )

        return

    # ── collect ──────────────────────────────────────────────────────
    rfq_row = repository.get_row(rfq_number)

    if rfq_row is None:
        logger.error(
            "Cannot record quotes — RFQ %s not found",
            rfq_number,
        )
        return

    if mode == "collect":
        quotes = output.get("quotes") or []

        for quote in quotes:
            repository.add_quote(
                rfq_row=rfq_row,
                vendor=quote["vendor"],
                total_amount=to_decimal(quote["total_amount"]),
                lines=quote.get("lines") or [],
                currency=quote.get("currency", "INR"),
                lead_time_days=quote.get("lead_time_days"),
                notes=quote.get("notes"),
                parse_status=quote.get(
                    "parse_status", "normalised"
                ),
                source_task_id=task.id,
            )

        if quotes:
            logger.info(
                "Recorded %d quote(s) against RFQ %s",
                len(quotes),
                rfq_number,
            )

        if rfq_row.status == "ISSUED":
            repository.set_status(rfq_number, "QUOTES_PENDING")

        return

    # ── compare (award) ─────────────────────────────────────────────
    awarded_vendor = output.get("awarded_vendor")

    if awarded_vendor:
        repository.set_awarded_vendor(rfq_number, awarded_vendor)

        logger.info(
            "RFQ %s awarded to %s",
            rfq_number,
            awarded_vendor,
        )


_RECORDERS = {
    "P1": _record_purchase_order,
    "P2": _record_rfq,
    "S1": _record_goods_receipt,
    "P4": _record_invoice_match,
    "F3": _record_books_posting,
}
