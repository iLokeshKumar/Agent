"""Vendor master persistence.

Intake reconciles every purchase request against this table: the first
request for a vendor creates the row, later requests find it. Matching
is case-insensitive on name so "ABC Technologies" and "abc
technologies" resolve to one vendor rather than two half-identities.
"""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.vendor import Vendor


logger = logging.getLogger(__name__)


class VendorRepository:

    def __init__(self, db: Session):
        self.db = db

    # ── Reads ────────────────────────────────────────────────────────

    def get_row(self, vendor_id: uuid.UUID) -> Vendor | None:
        return self.db.get(Vendor, vendor_id)

    def get_by_name(self, name: str) -> Vendor | None:
        """Case-insensitive lookup by exact name."""
        if not name:
            return None

        return self.db.execute(
            select(Vendor)
            .where(func.lower(Vendor.name) == name.strip().lower())
            .limit(1)
        ).scalars().first()

    def list_active(self) -> list[Vendor]:
        return self.db.execute(
            select(Vendor)
            .where(Vendor.is_active.is_(True))
            .order_by(Vendor.name)
        ).scalars().all()

    # ── Writes ───────────────────────────────────────────────────────

    def get_or_create(
        self,
        name: str,
        contact_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        address: str | None = None,
    ) -> Vendor:
        """Return the vendor by (case-insensitive) name, or create it.

        No commit — the caller's transaction owns the outcome. Two
        workers racing on a brand-new vendor can still both insert;
        the UNIQUE on ``name`` makes one of them fail, and the caller
        retries the request once against the winner's row.
        """
        canonical = (name or "").strip()

        existing = self.get_by_name(canonical)

        if existing is not None:
            return existing

        # Store the canonical capitalisation of the first sighting.
        vendor = Vendor(
            id=uuid.uuid4(),
            name=canonical,
            contact_name=contact_name,
            email=email,
            phone=phone,
            address=address,
        )

        self.db.add(vendor)

        try:
            self.db.flush()
        except Exception:
            self.db.rollback()
            existing = self.get_by_name(canonical)

            if existing is None:
                raise

            return existing

        logger.info("Registered new vendor %s", canonical)

        return vendor
