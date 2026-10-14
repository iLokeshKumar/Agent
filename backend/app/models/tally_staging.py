"""Tally staging — the stage-and-approve buffer between Books and Tally.

Tally is the statutory ledger of record. Agents never write to it
directly. A voucher lands here as STAGED, a human (or the accountant
console) moves it to APPROVED, and only then can ``post_approved``
push it into Tally. The invariant is structural: the post path filters
on ``status == APPROVED`` before anything leaves the building, so an
unapproved voucher is unpostable by construction — not by discipline.

Uniqueness on ``(entity, books_txn_id)`` makes staging idempotent: a
task that somehow runs twice restages nothing.
"""

import uuid
from datetime import datetime, date

from sqlalchemy import DateTime, Date, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class TallyStagingVoucher(Base):
    __tablename__ = "tally_staging_voucher"
    __table_args__ = (
        UniqueConstraint("entity", "books_txn_id", "line_no"),
        Index("ix_tally_staging_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Which company's books this belongs to — Yexis Electronics vs
    # Yexis Solutions must never blur.
    entity: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # The Books-side transaction that produced this voucher (invoice
    # number, journal number, ...). The idempotency anchor.
    books_txn_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    line_no: Mapped[int] = mapped_column(
        nullable=False,
    )

    # Journal | Sales | Purchase | Receipt | Payment | Credit Note ...
    voucher_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="Journal",
    )

    voucher_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    narration: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    tally_account: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    debit: Mapped[float] = mapped_column(
        nullable=False,
        server_default="0",
    )

    credit: Mapped[float] = mapped_column(
        nullable=False,
        server_default="0",
    )

    # Rendered Tally XML import envelope for this voucher line.
    voucher_xml: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # STAGED → APPROVED → POSTED
    #            ↘ REJECTED      ↘ POST_FAILED
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="STAGED",
    )

    approved_by: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # What Tally returned when the voucher was accepted.
    tally_voucher_no: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    correlation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
