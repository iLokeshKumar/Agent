"""Books → Tally account mapping table.

Zoho Books account names differ from TallyPrime ledger names. F3 uses
this table to translate every Books account to the correct Tally ledger
before staging vouchers. The finance owner fills it; the platform never
auto-populates rows. An unmapped account is a staging error, surfaced
as POST_FAILED rather than silently using a wrong ledger.

The table is intentionally sparse at first — only accounts that have
actually appeared in synced transactions need rows.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class BooksTallyMap(Base):
    """One row = one Books account ↔ Tally ledger mapping per entity."""

    __tablename__ = "books_tally_map"
    __table_args__ = (
        UniqueConstraint("entity", "books_account_id", name="uq_btm_entity_account"),
        Index("ix_btm_entity_active", "entity", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    # Which legal entity this mapping applies to.
    entity: Mapped[str] = mapped_column(String(20), nullable=False)

    # Zoho Books identifiers — kept stable even if the display name changes.
    books_account_id: Mapped[str] = mapped_column(String(100), nullable=False)
    books_account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    books_account_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Tally target — exact names must match the Tally ledger master.
    tally_ledger_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tally_group: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Optional cost centre to attach to this account's voucher legs.
    cost_centre: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # True = mapping is valid and in use; False = suspended (needs review).
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Who mapped it and when.
    mapped_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mapped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Freeform notes from the finance owner (e.g. "confirmed with auditor").
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=datetime.utcnow, onupdate=datetime.utcnow,
    )
