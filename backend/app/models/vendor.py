"""Vendor master table.

Until now a vendor was whatever string the caller typed into the
purchase request, repeated verbatim onto every downstream document. A
master row gives the workflow one identity per vendor to hang contact
details and history off, and lets intake reconcile spelling variants —
``get_or_create`` matches case-insensitively on name, so "ABC
Technologies" and "abc technologies" land on the same vendor.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )

    # Case-insensitive uniqueness is enforced by the repository's
    # get_or_create (lower(name)); this UNIQUE is the backstop for the
    # canonical stored form.
    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
    )

    contact_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    email: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    address: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
