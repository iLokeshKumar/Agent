"""Durable document numbering.

Every business document number (PO, GRN, invoice) comes from a Postgres
sequence rather than an in-process counter. A class variable restarts
with the process and is not shared between workers, which is how the
same PO number came to be issued five times before this existed.

``nextval`` is non-transactional: two callers can never receive the same
value, and a rollback does not hand a number back. Uniqueness is still
enforced at the table (``purchase_number``, ``grn_number``,
``invoice_number`` are all UNIQUE) — the sequence avoids collisions, the
constraint proves it.
"""

from datetime import datetime

from sqlalchemy import text

from app.core.db import SessionLocal


PO_SEQUENCE = "po_number_seq"
GRN_SEQUENCE = "grn_number_seq"
RFQ_SEQUENCE = "rfq_number_seq"

# Invoice numbers are deliberately absent: they are the supplier's, not
# ours. Generating one would invent a document that exists on paper.


def next_document_number(
    prefix: str,
    sequence: str,
    now: datetime,
) -> str:
    """Return the next number for `prefix`, e.g. ``PO-202608-01007``.

    Uses its own short-lived session: the number must be drawn
    independently of whatever transaction the caller is in, so a later
    rollback cannot make it look available again.
    """
    db = SessionLocal()

    try:
        counter = db.execute(
            # Cast rather than interpolate — the name never reaches the
            # statement text.
            text("SELECT nextval(CAST(:sequence AS regclass))"),
            {"sequence": sequence},
        ).scalar()
        db.commit()
    finally:
        db.close()

    return f"{prefix}-{now.strftime('%Y%m')}-{counter:05d}"
