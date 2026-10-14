"""Task chaining — what happens after an agent succeeds.

One purchase request should travel the whole Purchase-to-Pay flow
without anyone posting the next task by hand:

    purchase_request  → P1 → PO raised
    grn_request       → S1 → goods received, serials captured
    invoice_matching  → P4 → three-way match
    books_to_tally    → F3 → posted

A chain step is nothing more than a QUEUED row. The worker polls the
database, so an INSERT is the whole mechanism — no scheduler, no
in-process queue, no new architecture.

Two rules the payloads follow:

* **By reference, never by value.** A child payload carries document
  numbers. The agent loads the document from the database, so a stale
  copy in a payload can never contradict what was actually ordered.
* **Nothing is invented.** If the supplier has not billed us there is no
  invoice number to pass on, so the chain stops after the goods receipt
  and resumes when an ``invoice_matching`` task arrives.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.documents import CONTRACT_VERSION, to_decimal
from app.agents.f3_books_to_tally import DEFAULT_ACCOUNT_MAP
from app.models.task import Task
from app.repository.invoice_repository import InvoiceRepository
from app.repository.purchase_order_repository import (
    PurchaseOrderRepository,
)
from app.repository.task_repository import TaskRepository


logger = logging.getLogger(__name__)


# Reserved key on a child payload. Underscore-prefixed so it reads as
# platform bookkeeping and not as business data an agent should act on.
CHAIN_KEY = "_chain"


def advance_chain(
    db: Session,
    task: Task,
    output: dict,
) -> Task | None:
    """Queue the next step of the workflow, if there is one.

    Returns the child task, or None when the chain legitimately ends
    here — an unchained task type, a missing document, or an invoice
    that has not arrived yet.
    """
    builder = _CHAINS.get(task.task_type)

    if builder is None:
        return None

    return builder(db, task, output or {})


# ── purchase_request → grn_request (Phase 5) ─────────────────────────


def _chain_purchase_to_grn(
    db: Session,
    task: Task,
    output: dict,
) -> Task | None:
    po_number = output.get("po_number")

    if not po_number:
        return None

    document = PurchaseOrderRepository(db).get_by_number(po_number)

    if document is None:
        # record_document declined to write it — chaining a receipt
        # against a PO that is not on file would defeat the point.
        logger.warning(
            "Not chaining a receipt: PO %s is not on file",
            po_number,
        )
        return None

    payload = {
        "po_number": document.po_number,
        "vendor": document.vendor,
        "items": [
            {
                "item": line.item,
                "quantity": float(line.quantity),
            }
            for line in document.items
        ],
    }

    return _spawn(db, task, "grn_request", payload)


# ── grn_request → invoice_matching (Phase 7) ─────────────────────────


def _chain_grn_to_invoice_match(
    db: Session,
    task: Task,
    output: dict,
) -> Task | None:
    grn = output.get("grn") or {}

    grn_number = grn.get("grn_number")
    po_number = grn.get("po_number")

    if not grn_number or not po_number:
        return None

    invoice = InvoiceRepository(db).get_unmatched_for_po(po_number)

    if invoice is None:
        # The one number the platform must never generate. Until the
        # supplier bills us there is nothing to match, so the workflow
        # parks here and resumes when an invoice_matching task arrives —
        # which finds this PO and GRN already on file.
        logger.info(
            "PO %s received on GRN %s; no invoice on file — chain "
            "stops here until one is posted",
            po_number,
            grn_number,
        )
        return None

    payload = {
        "po_number": po_number,
        "grn_number": grn_number,
        "invoice_number": invoice.invoice_number,
    }

    return _spawn(db, task, "invoice_matching", payload)


# ── invoice_matching → books_to_tally (F3 close-out) ─────────────────


def _chain_invoice_to_books(
    db: Session,
    task: Task,
    output: dict,
) -> Task | None:
    match = output.get("match_result") or {}

    invoice_number = match.get("invoice_number")

    if not invoice_number:
        return None

    matched = match.get("match_status") == "FULLY_MATCHED"
    approved = bool(match.get("approved_despite_mismatch"))

    if not (matched or approved):
        logger.info(
            "Invoice %s is %s — not posting to books",
            invoice_number,
            match.get("match_status"),
        )
        return None

    invoice = InvoiceRepository(db).get_by_number(invoice_number)

    if invoice is None:
        logger.warning(
            "Not posting to books: invoice %s is not on file",
            invoice_number,
        )
        return None

    payload = _books_payload(invoice, approved)

    return _spawn(db, task, "books_to_tally", payload)


def _books_payload(invoice, approved: bool) -> dict:
    """A one-invoice purchase voucher, in the shape F3 normalises.

    Dr Purchases / Cr the vendor, for the invoice amount — balanced by
    construction, which is what F3's reconciliation report checks.
    """
    vendor = invoice.vendor or "Sundry Creditors"
    amount = float(to_decimal(invoice.invoice_amount))

    # F3 refuses a period with no start date, and invoice_date is
    # optional on a supplier invoice. An undated invoice posts on the day
    # it reached the books, which is the date the platform can actually
    # vouch for.
    invoice_date = invoice.invoice_date or datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d")

    narration = f"Purchase against {invoice.po_number}"

    if approved:
        narration += " (mismatch approved)"

    entry = {
        "date": invoice_date,
        "reference": invoice.invoice_number,
        "narration": narration,
        "voucher_type": "Purchase",
    }

    return {
        # A single invoice is a one-day period.
        "start_date": invoice_date,
        "end_date": invoice_date,
        # F3 *replaces* its defaults when account_map is supplied, so the
        # vendor ledger has to be merged in — passing it alone would
        # report every standard account as UNMAPPED.
        "account_map": DEFAULT_ACCOUNT_MAP | {vendor: vendor},
        "journal_entries": [
            {**entry, "account": "Purchases", "debit": amount, "credit": 0},
            {**entry, "account": vendor, "debit": 0, "credit": amount},
        ],
        # Read back by record_document to close out the documents. F3
        # itself is period-based and ignores them.
        "invoice_number": invoice.invoice_number,
        "po_number": invoice.po_number,
    }


# ── Spawning ─────────────────────────────────────────────────────────


def _spawn(
    db: Session,
    parent: Task,
    task_type: str,
    payload: dict,
) -> Task | None:
    """Queue a child task, exactly once per parent.

    Dedup is on (parent task, task type) — deliberately **not** on the
    document number. A second genuine delivery against the same PO is a
    second GRN and must stay possible; what must not happen is one
    completed parent spawning the same child twice.
    """
    existing = _already_spawned(db, parent.id, task_type)

    if existing is not None:
        logger.info(
            "Task %s already spawned %s (%s) — not spawning again",
            parent.id,
            task_type,
            existing.id,
        )
        return None

    child_payload = dict(payload)
    child_payload[CHAIN_KEY] = {
        "parent_task_id": str(parent.id),
        "parent_type": parent.task_type,
        "contract_version": CONTRACT_VERSION,
    }

    child = TaskRepository(db).create(
        task_type=task_type,
        source="chain",
        priority=parent.priority,
        requested_by=parent.requested_by,
        payload=child_payload,
        # Inherited: the whole flow is one trace in the ledger.
        correlation_id=parent.correlation_id,
    )

    logger.info(
        "Chained %s -> %s (task %s, correlation %s)",
        parent.task_type,
        task_type,
        child.id,
        parent.correlation_id,
    )

    return child


def _already_spawned(
    db: Session,
    parent_task_id: uuid.UUID,
    task_type: str,
) -> Task | None:
    return db.execute(
        select(Task)
        .where(
            Task.task_type == task_type,
            Task.payload[CHAIN_KEY]["parent_task_id"].astext
            == str(parent_task_id),
        )
        .limit(1)
    ).scalars().first()


_CHAINS = {
    "purchase_request": _chain_purchase_to_grn,
    "grn_request": _chain_grn_to_invoice_match,
    "invoice_matching": _chain_invoice_to_books,
}
