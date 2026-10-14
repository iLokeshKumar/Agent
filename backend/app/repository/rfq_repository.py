"""RFQ persistence.

The RFQ and its quotes are P2's working documents: the comparison pack
the buyer awards from is built from these rows, never from a copy
passed along in a task payload. Everything downstream — the award,
P1's PO — loads by ``rfq_number`` from here.
"""

import logging
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.documents import (
    QuoteLine,
    RfqDocument,
    QuoteSubmission,
    to_decimal,
)
from app.models.rfq import Rfq, RfqQuote


logger = logging.getLogger(__name__)


class RfqRepository:

    def __init__(self, db: Session):
        self.db = db

    # ── Reads ────────────────────────────────────────────────────────

    def get_row(self, rfq_number: str) -> Rfq | None:
        return self.db.execute(
            select(Rfq)
            .where(Rfq.rfq_number == rfq_number)
            .limit(1)
        ).scalars().first()

    def get_by_number(
        self,
        rfq_number: str,
    ) -> RfqDocument | None:
        """The RFQ as a contract document, or None if never issued."""
        row = self.get_row(rfq_number)

        if row is None:
            return None

        return self.to_document(row)

    def get_by_task(self, task_id: uuid.UUID) -> Rfq | None:
        """Keeps RFQ issuance idempotent across task retries."""
        return self.db.execute(
            select(Rfq)
            .where(Rfq.task_id == task_id)
            .limit(1)
        ).scalars().first()

    def latest_quote_for_vendor(
        self,
        rfq_id: uuid.UUID,
        vendor: str,
    ) -> RfqQuote | None:
        """The vendor's newest revision, by (rfq, vendor, revision)."""
        return self.db.execute(
            select(RfqQuote)
            .where(
                RfqQuote.rfq_id == rfq_id,
                RfqQuote.vendor == vendor,
            )
            .order_by(RfqQuote.revision.desc())
            .limit(1)
        ).scalars().first()

    def list_quotes(self, rfq_id: uuid.UUID) -> list[RfqQuote]:
        """Every quote revision, lowest revision first."""
        return list(
            self.db.execute(
                select(RfqQuote)
                .where(RfqQuote.rfq_id == rfq_id)
                .order_by(RfqQuote.revision.asc())
            ).scalars().all()
        )

    def to_document(self, row: Rfq) -> RfqDocument:
        """The comparison pack as a contract document.

        Only each vendor's latest revision is compared — a revised
        quote supersedes the one before it.
        """
        latest: dict[str, RfqQuote] = {}

        for quote in row.quotes:
            current = latest.get(quote.vendor)

            if (
                current is None
                or quote.revision > current.revision
            ):
                latest[quote.vendor] = quote

        submissions = [
            QuoteSubmission(
                vendor=quote.vendor,
                total_amount=quote.total_amount,
                currency=quote.currency,
                lead_time_days=quote.lead_time_days,
                lines=[
                    QuoteLine(
                        item=line.get("item", ""),
                        quantity=to_decimal(line.get("quantity")),
                        unit_price=to_decimal(line.get("unit_price")),
                    )
                    for line in (quote.lines or [])
                ],
                parse_status=quote.parse_status,
                notes=quote.notes,
                received_at=quote.created_at.isoformat(),
            )
            for quote in latest.values()
        ]

        return RfqDocument(
            rfq_number=row.rfq_number,
            vendor=row.vendor or "",
            status=row.status,
            awarded_vendor=row.awarded_vendor,
            items=[
                QuoteLine(
                    item=item.get("item", ""),
                    quantity=to_decimal(item.get("quantity")),
                    unit_price=to_decimal(item.get("unit_price")),
                )
                for item in (row.payload.get("items") or [])
            ],
            quotes=submissions,
            issued_at=row.issued_at.isoformat()
            if row.issued_at
            else None,
        )

    # ── Writes ───────────────────────────────────────────────────────

    def create(
        self,
        task_id: uuid.UUID,
        correlation_id: uuid.UUID,
        rfq_number: str,
        items: list[dict],
        vendor: str | None = None,
        status: str = "ISSUED",
        payload: dict | None = None,
    ) -> Rfq:
        """Persist an RFQ.

        No flush/commit here — the caller owns the transaction so the
        document and the task status land together.
        """
        rfq = Rfq(
            id=uuid.uuid4(),
            task_id=task_id,
            correlation_id=correlation_id,
            rfq_number=rfq_number,
            vendor=vendor,
            status=status,
            payload=payload or {},
        )

        self.db.add(rfq)

        return rfq

    def add_quote(
        self,
        rfq_row: Rfq,
        vendor: str,
        total_amount: Decimal,
        lines: list[dict],
        currency: str = "INR",
        lead_time_days: int | None = None,
        notes: str | None = None,
        parse_status: str = "normalised",
        source_task_id: uuid.UUID | None = None,
    ) -> RfqQuote | None:
        """Append one quote revision for a vendor.

        Revision numbers are per (rfq, vendor), drawn from the highest
        already stored. When ``source_task_id`` is given and that task
        already filed a quote for this vendor, nothing is written —
        a retried task must not double the vendor's quote.

        No commit — the caller owns the transaction. Returns the row,
        or None when the quote was already on file.
        """
        if source_task_id is not None:
            existing = self.db.execute(
                select(RfqQuote)
                .where(
                    RfqQuote.rfq_id == rfq_row.id,
                    RfqQuote.vendor == vendor,
                    RfqQuote.source_task_id == source_task_id,
                )
                .limit(1)
            ).scalars().first()

            if existing is not None:
                logger.info(
                    "Quote from %s for RFQ %s already filed by task "
                    "%s — not appending again",
                    vendor,
                    rfq_row.rfq_number,
                    source_task_id,
                )
                return None

        latest = self.latest_quote_for_vendor(rfq_row.id, vendor)

        revision = (
            latest.revision + 1 if latest is not None else 1
        )

        quote = RfqQuote(
            id=uuid.uuid4(),
            rfq_id=rfq_row.id,
            vendor=vendor,
            revision=revision,
            total_amount=to_decimal(total_amount),
            currency=currency,
            lead_time_days=lead_time_days,
            lines=lines,
            notes=notes,
            parse_status=parse_status,
            source_task_id=source_task_id,
        )

        self.db.add(quote)

        return quote

    def set_status(self, rfq_number: str, status: str) -> str | None:
        """Advance the RFQ's workflow state. Returns the new status."""
        row = self.get_row(rfq_number)

        if row is None:
            logger.warning(
                "Cannot set status on unknown RFQ %s", rfq_number
            )
            return None

        if row.status == status:
            return status

        logger.info(
            "RFQ %s status %s -> %s",
            rfq_number, row.status, status,
        )

        row.status = status

        return status

    def set_awarded_vendor(
        self,
        rfq_number: str,
        vendor: str,
    ) -> str | None:
        """Record the buyer's award. Returns the vendor, or None."""
        row = self.get_row(rfq_number)

        if row is None:
            logger.warning(
                "Cannot award unknown RFQ %s", rfq_number
            )
            return None

        row.awarded_vendor = vendor

        if row.status in ("ISSUED", "QUOTES_PENDING"):
            row.status = "AWARDED"

        return vendor
