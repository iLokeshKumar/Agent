"""Tally Gateway — invariant tests.

The gateway's whole point is what it refuses to do:

    * post_approved only touches rows whose status is exactly APPROVED
    * staging is idempotent per (entity, books_txn_id)
    * entity must be named and known — no cross-entity blur
    * a transaction posts complete or not at all

Runs against SQLite in-memory so no Tally, no Postgres.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.agent import Base
from app.models.tally_staging import TallyStagingVoucher
from app.integrations.tally_gateway import (
    SimulatedTallyTransport,
    TallyGateway,
    TallyGatewayError,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # JSONB works on SQLite via SQLAlchemy's portable JSON variant;
    # gen_random_uuid server defaults are not used (ids are client-side).
    Base.metadata.create_all(
        bind=engine,
        tables=[TallyStagingVoucher.__table__],
    )
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


ENTRIES = [
    {
        "date": date(2026, 8, 1),
        "reference": "INV-100",
        "narration": "Purchase of laptops",
        "tally_account": "Purchase Accounts",
        "debit": 75000.0,
        "credit": 0.0,
        "voucher_type": "Journal",
    },
    {
        "date": date(2026, 8, 1),
        "reference": "INV-100",
        "narration": "Purchase of laptops",
        "tally_account": "Sundry Creditors",
        "debit": 0.0,
        "credit": 75000.0,
        "voucher_type": "Journal",
    },
]


class TestStaging:

    def test_stage_creates_staged_rows(self, db):
        rows = TallyGateway(db).stage_entries(
            "YEXIS-EL", "INV-100", ENTRIES
        )
        assert len(rows) == 2
        assert all(r.status == "STAGED" for r in rows)
        assert [r.line_no for r in rows] == [1, 2]
        # One envelope per transaction, carried on the first line.
        assert rows[0].voucher_xml and "<VOUCHER " in rows[0].voucher_xml
        assert rows[1].voucher_xml is None

    def test_stage_is_idempotent(self, db):
        gateway = TallyGateway(db)
        first = gateway.stage_entries("YEXIS-EL", "INV-100", ENTRIES)
        second = gateway.stage_entries("YEXIS-EL", "INV-100", ENTRIES)
        assert len(first) == 2
        assert second == first
        assert gateway.list_for_transaction("YEXIS-EL", "INV-100") == first

    def test_unknown_entity_refused(self, db):
        with pytest.raises(TallyGatewayError):
            TallyGateway(db).stage_entries(
                "ACME", "INV-100", ENTRIES
            )

    def test_empty_entries_stage_nothing(self, db):
        assert TallyGateway(db).stage_entries("YEXIS-EL", "X", []) == []


class TestApproval:

    def _staged(self, db):
        return TallyGateway(db).stage_entries("YEXIS-EL", "INV-100", ENTRIES)

    def test_approve_moves_status(self, db):
        rows = self._staged(db)
        result = TallyGateway(db).approve(
            [r.id for r in rows], "accountant"
        )
        assert result["approved"] == 2
        assert all(r.status == "APPROVED" for r in rows)
        assert all(r.approved_by == "accountant" for r in rows)

    def test_approve_twice_counts_but_does_not_duplicate(self, db):
        rows = self._staged(db)
        ids = [r.id for r in rows]
        gateway = TallyGateway(db)
        gateway.approve(ids, "accountant")
        again = gateway.approve(ids, "accountant")
        assert again["approved"] == 0
        assert again["already_approved"] == 2

    def test_reject_staged_only(self, db):
        rows = self._staged(db)
        count = TallyGateway(db).reject([rows[0].id], "wrong account")
        assert count == 1
        assert rows[0].status == "REJECTED"


class TestPostingInvariant:

    def _gateway_with(self, db, status_flow):
        gateway = TallyGateway(db)
        rows = gateway.stage_entries("YEXIS-EL", "INV-100", ENTRIES)
        if "approve" in status_flow:
            gateway.approve([r.id for r in rows], "accountant")
        return gateway, rows

    def test_unapproved_voucher_is_never_posted(self, db):
        """THE invariant: STAGED rows cannot be pushed, ever."""
        gateway, rows = self._gateway_with(db, ["stage"])

        result = gateway.post_approved("YEXIS-EL")

        assert result["posted_transactions"] == []
        assert all(r.status == "STAGED" for r in rows)

    def test_posted_rows_come_only_from_approved(self, db):
        gateway, rows = self._gateway_with(db, ["stage", "approve"])

        result = gateway.post_approved("YEXIS-EL")

        assert len(result["posted_transactions"]) == 1
        txn = result["posted_transactions"][0]
        assert txn["books_txn_id"] == "INV-100"
        assert txn["lines"] == 2
        assert txn["tally_voucher_no"]
        assert all(r.status == "POSTED" for r in rows)

    def test_post_is_one_shot(self, db):
        gateway, rows = self._gateway_with(db, ["stage", "approve"])
        gateway.post_approved("YEXIS-EL")
        again = gateway.post_approved("YEXIS-EL")
        # Nothing left approved — the same voucher cannot double-post.
        assert again["posted_transactions"] == []

    def test_post_failure_marks_post_failed_not_silent(self, db):
        gateway, rows = self._gateway_with(db, ["stage", "approve"])

        import app.integrations.tally_gateway as mod

        class FailingTransport(SimulatedTallyTransport):
            def import_vouchers(self, entity, xml):
                raise RuntimeError("connection refused")

        original = mod._transport
        mod._transport = lambda: FailingTransport()
        try:
            result = gateway.post_approved("YEXIS-EL")
        finally:
            mod._transport = original

        assert result["posted_transactions"] == []
        assert len(result["refused"]) == 1
        assert all(r.status == "POST_FAILED" for r in rows)
        assert "connection refused" in rows[0].error_message

    def test_wrong_entity_at_post_time_refused(self, db):
        self._gateway_with(db, ["stage", "approve"])
        with pytest.raises(TallyGatewayError):
            TallyGateway(db).post_approved("OTHER")


class TestTransport:

    def test_simulated_transport_mints_voucher_numbers(self):
        transport = SimulatedTallyTransport()
        response = transport.import_vouchers("YEXIS-EL", "<ENVELOPE/>")
        assert response["accepted"] is True
        assert response["tally_voucher_no"].startswith("TV-")
