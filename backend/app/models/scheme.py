"""F2 Scheme Claims — scheme register + claim lifecycle.

Local DB tables per Cowork Tech Spec §7.2 (scheme_master, scheme_claim).
The register is human-verified before any claim computes against it —
a parsed circular is a *candidate*, never truth (the F2 spec's
"verified_by" gate).

Import in app/main.py::_init_database() so Base.metadata creates them.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class SchemeMaster(Base):
    """Vendor scheme register — parsed circulars land here as
    candidates; `verified_by` is the human gate before F2 computes."""
    __tablename__ = "scheme_master"

    scheme_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    vendor: Mapped[str] = mapped_column(String(100), nullable=False)  # Samsung/…
    scheme_type: Mapped[str] = mapped_column(String(50), nullable=False)  # slab | bundle | price_support
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    eligibility_rules_json: Mapped[dict | None] = mapped_column(JSONB)  # eligible_skus, per_invoice_min, slabs
    evidence_reqs_json: Mapped[dict | None] = mapped_column(JSONB)
    circular_doc_ref: Mapped[str | None] = mapped_column(String(200))  # doc_index ref
    parsed_confidence: Mapped[float | None] = mapped_column(Float)
    verified_by: Mapped[str | None] = mapped_column(String(100))  # None ⇒ NOT verified
    verified_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="candidate")  # candidate|verified|expired
    created_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


class SchemeClaim(Base):
    """Claim lifecycle to the 99% accuracy target: computed → packed →
    submitted → (partial|settled|disputed). One scheme per dealer per
    claim period; amounts reconcile against the credit notes received."""
    __tablename__ = "scheme_claim"
    __table_args__ = (
        UniqueConstraint("scheme_id", "dealer_ref", name="uq_scheme_claim"),
        Index("ix_scheme_claim_status", "status"),
    )

    claim_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    scheme_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("scheme_master.scheme_id"), nullable=False
    )
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    dealer_ref: Mapped[str | None] = mapped_column(String(100))
    amount_computed: Mapped[float] = mapped_column(Float, default=0)
    amount_claimed: Mapped[float | None] = mapped_column(Float)
    claimed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount_received: Mapped[float | None] = mapped_column(Float)
    credit_note_refs_json: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="computed")  # computed|packed|submitted|partial|settled|disputed
    variance_reason: Mapped[str | None] = mapped_column(Text)
    computation_json: Mapped[dict | None] = mapped_column(JSONB)  # the earned_claim() output — full audit
    created_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
