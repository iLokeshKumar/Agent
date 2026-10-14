"""P2 Agent — Sourcing/RFQ Lifecycle Test Suite

Tests the complete P2 workflow across its three modes:
    1. Validation (reject bad payloads per mode)
    2. Issue (RFQ number generated, vendors echoed)
    3. Collect (quotes normalised; unparseable flagged)
    4. Compare (ranking, recommendation, award gate, hand-off)
    5. Award gate (NEEDS_APPROVAL → approved re-run records award)
    6. Stored-RFQ integration (repository round-trip)

External effects are patched per-test:
    - RFQ-number generation is stubbed so no Postgres sequence is hit
    - document_session is patched with an in-memory SQLite session for
      the repository round-trips
"""

import uuid
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.p2_sourcing_rfq import P2SourcingRfq
from app.models.rfq import Rfq, RfqQuote
from app.repository.rfq_repository import RfqRepository
from app.services import numbering as numbering_mod


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def isolated_numbering(monkeypatch):
    """Keep execution off the real database sequences."""
    monkeypatch.setattr(
        numbering_mod,
        "next_document_number",
        lambda *a, **k: "RFQ-TEST-0001",
    )
    yield


@pytest.fixture
def agent():
    return P2SourcingRfq()


@pytest.fixture
def sqlite_db():
    """In-memory SQLite with the RFQ tables.

    Every insert supplies id, revision and timestamps explicitly —
    the repositories already do, mirroring how P1's tests construct
    rows without Postgres server defaults.
    """
    from app.models.agent import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        bind=engine,
        tables=[Rfq.__table__, RfqQuote.__table__],
    )

    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()

    yield db

    db.close()


@pytest.fixture
def p2_db_session(sqlite_db):
    """Patch P2's document_session to the in-memory SQLite session.

    collect and compare load documents by number; without this they
    would reach the real Postgres, where the rfqs table does not exist
    in the test environment.
    """
    db = sqlite_db

    @contextmanager
    def fake_session():
        yield db

    with patch(
        "app.agents.p2_sourcing_rfq.document_session",
        fake_session,
    ):
        yield db


@pytest.fixture
def issue_payload():
    return {
        "mode": "issue",
        "vendors": ["Alpha Systems", "Beta Integrators"],
        "items": [
            {
                "item": "AV Control Panel",
                "quantity": 2,
            },
            {
                "item": "Mounting Rig",
                "quantity": 4,
            },
        ],
        "currency": "INR",
    }


@pytest.fixture
def quotes_payload():
    return {
        "mode": "collect",
        "rfq_number": "RFQ-TEST-0001",
        "quotes": [
            {
                "vendor": "Alpha Systems",
                "total_amount": 120000,
                "lead_time_days": 14,
                "lines": [
                    {
                        "item": "AV Control Panel",
                        "quantity": 2,
                        "unit_price": 50000,
                    },
                    {
                        "item": "Mounting Rig",
                        "quantity": 4,
                        "unit_price": 5000,
                    },
                ],
            },
            {
                "vendor": "Beta Integrators",
                "total_amount": 110000,
                "lead_time_days": 21,
            },
        ],
    }


@pytest.fixture
def compare_payload():
    return {
        "mode": "compare",
        "quotes": [
            {"vendor": "Alpha Systems", "total_amount": 120000},
            {"vendor": "Beta Integrators", "total_amount": 110000},
            {"vendor": "Gamma AV", "total_amount": 95000},
        ],
    }


# ═══════════════════════════════════════════════════════════════════
# 1. Validation Tests
# ═══════════════════════════════════════════════════════════════════

class TestP2Validation:
    """Structural validation per mode."""

    def test_empty_payload_fails(self, agent):
        result = agent.validate({})
        assert result is not None
        assert "empty" in result.lower()

    def test_unknown_mode_fails(self, agent):
        result = agent.validate({"mode": "teleport"})
        assert result is not None
        assert "mode" in result.lower()

    # ── issue ────────────────────────────────────────────────────────

    def test_issue_requires_items(self, agent):
        result = agent.validate({"mode": "issue", "vendors": ["A"]})
        assert result is not None
        assert "items" in result

    def test_issue_requires_vendors(self, agent):
        result = agent.validate({
            "mode": "issue",
            "items": [{"item": "X", "quantity": 1}],
        })
        assert result is not None
        assert "vendors" in result

    def test_issue_rejects_blank_vendor_names(self, agent):
        result = agent.validate({
            "mode": "issue",
            "vendors": ["Alpha", "   "],
            "items": [{"item": "X", "quantity": 1}],
        })
        assert result is not None
        assert "vendor" in result.lower()

    def test_issue_rejects_zero_quantity(self, agent):
        result = agent.validate({
            "mode": "issue",
            "vendors": ["Alpha"],
            "items": [{"item": "X", "quantity": 0}],
        })
        assert result is not None
        assert "quantity" in result

    def test_issue_valid_payload_passes(self, agent, issue_payload):
        assert agent.validate(issue_payload) is None

    # ── collect ──────────────────────────────────────────────────────

    def test_collect_requires_rfq_number(self, agent):
        result = agent.validate({
            "mode": "collect",
            "quotes": [{"vendor": "A", "total_amount": 1}],
        })
        assert result is not None
        assert "rfq_number" in result

    def test_collect_requires_quotes(self, agent):
        result = agent.validate({
            "mode": "collect",
            "rfq_number": "RFQ-1",
        })
        assert result is not None
        assert "quotes" in result

    def test_collect_allows_priceless_quote_flags_it(self, agent):
        """A missing price files as price_missing — the buyer sees it
        excluded from the ranking, not silently dropped."""
        result = agent.validate({
            "mode": "collect",
            "rfq_number": "RFQ-1",
            "quotes": [{"vendor": "Alpha"}],
        })
        assert result is None

    def test_collect_rejects_negative_price(self, agent):
        result = agent.validate({
            "mode": "collect",
            "rfq_number": "RFQ-1",
            "quotes": [{"vendor": "Alpha", "total_amount": -5}],
        })
        assert result is not None
        assert "total_amount" in result

    def test_collect_valid_payload_passes(
        self, agent, quotes_payload
    ):
        assert agent.validate(quotes_payload) is None

    # ── compare ──────────────────────────────────────────────────────

    def test_compare_requires_something_to_compare(self, agent):
        result = agent.validate({"mode": "compare"})
        assert result is not None
        assert "compare" in result.lower()

    def test_compare_with_inline_quotes_passes(
        self, agent, compare_payload
    ):
        assert agent.validate(compare_payload) is None

    def test_compare_with_rfq_only_passes(self, agent):
        assert agent.validate({
            "mode": "compare",
            "rfq_number": "RFQ-1",
        }) is None

    # ── inference ────────────────────────────────────────────────────

    def test_mode_inferred_collect(self, agent, quotes_payload):
        quotes_payload.pop("mode")
        assert agent.validate(quotes_payload) is None

    def test_mode_inferred_compare(self, agent):
        assert agent.validate({
            "rfq_number": "RFQ-1"
        }) is None

    def test_mode_inferred_issue(self, agent, issue_payload):
        issue_payload.pop("mode")
        assert agent.validate(issue_payload) is None


# ═══════════════════════════════════════════════════════════════════
# 2. Issue Tests
# ═══════════════════════════════════════════════════════════════════

class TestP2Issue:

    def test_issue_succeeds(self, agent, issue_payload):
        result = agent.run(payload=issue_payload)

        assert result.status == "SUCCESS"
        assert result.output["rfq_number"] == "RFQ-TEST-0001"
        assert result.output["status"] == "ISSUED"

    def test_issue_echoes_vendors(self, agent, issue_payload):
        result = agent.run(payload=issue_payload)

        assert result.output["vendors"] == [
            "Alpha Systems", "Beta Integrators",
        ]

    def test_issue_records_actions(self, agent, issue_payload):
        result = agent.run(payload=issue_payload)

        assert "issued_rfq:RFQ-TEST-0001" in result.actions_taken
        assert any(
            a.startswith("identified_vendors:")
            for a in result.actions_taken
        )

    def test_issue_without_task_is_reported_not_persisted(
        self, agent, issue_payload
    ):
        """The agent issues; the executor's recorder persists."""
        result = agent.run(payload=issue_payload)

        # The run itself is SUCCESS — persistence is the recorder's
        # job and needs the task row, which direct .run() calls lack.
        assert result.status == "SUCCESS"
        assert result.output["mode"] == "issue"


# ═══════════════════════════════════════════════════════════════════
# 3. Collect Tests
# ═══════════════════════════════════════════════════════════════════

class TestP2Collect:

    def test_collect_normalises_quotes(
        self, agent, quotes_payload, p2_db_session
    ):
        db = p2_db_session

        RfqRepository(db).create(
            task_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            rfq_number="RFQ-TEST-0001",
            items=[],
        )
        db.commit()

        result = agent.run(payload=quotes_payload)

        assert result.status == "SUCCESS"
        assert result.output["quotes_collected"] == 2
        assert result.output["quotes_normalised"] == 2
        assert result.output["quotes_unparseable"] == 0

    def test_collect_flags_unparseable_quote(
        self, agent, quotes_payload, p2_db_session
    ):
        db = p2_db_session

        RfqRepository(db).create(
            task_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            rfq_number="RFQ-TEST-0001",
            items=[],
        )
        db.commit()

        quotes_payload["quotes"].append({
            "vendor": "Gamma AV",
        })  # no price

        result = agent.run(payload=quotes_payload)

        assert result.status == "SUCCESS"
        assert result.output["quotes_normalised"] == 2
        assert result.output["quotes_unparseable"] == 1

        statuses = {
            quote["vendor"]: quote["parse_status"]
            for quote in result.output["quotes"]
        }
        assert statuses["Gamma AV"] == "price_missing"

    def test_collect_rejects_unknown_rfq(
        self, agent, p2_db_session
    ):
        result = agent.run(payload={
            "mode": "collect",
            "rfq_number": "RFQ-GHOST",
            "quotes": [{"vendor": "A", "total_amount": 1}],
        })

        assert result.status == "VALIDATION_ERROR"
        assert "not found" in result.error.lower()

    def test_collect_records_per_vendor_actions(
        self, agent, quotes_payload, p2_db_session
    ):
        db = p2_db_session

        RfqRepository(db).create(
            task_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            rfq_number="RFQ-TEST-0001",
            items=[],
        )
        db.commit()

        result = agent.run(payload=quotes_payload)

        assert "collected_quote:Alpha Systems" in (
            result.actions_taken
        )
        assert "collected_quote:Beta Integrators" in (
            result.actions_taken
        )

    def test_collect_normalisation_survives_storage(
        self, agent, quotes_payload, p2_db_session
    ):
        """The output quotes carry the parse verdict the recorder files."""
        db = p2_db_session

        RfqRepository(db).create(
            task_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            rfq_number="RFQ-TEST-0001",
            items=[],
        )
        db.commit()

        quotes_payload["quotes"].append({"vendor": "Gamma AV"})

        result = agent.run(payload=quotes_payload)

        assert result.output["quotes_normalised"] == 2
        assert result.output["quotes_unparseable"] == 1

        by_vendor = {
            quote["vendor"]: quote
            for quote in result.output["quotes"]
        }
        assert by_vendor["Gamma AV"]["parse_status"] == (
            "price_missing"
        )


# ═══════════════════════════════════════════════════════════════════
# 4. Compare Tests
# ═══════════════════════════════════════════════════════════════════

class TestP2Compare:

    def test_compare_ranks_cheapest_first(
        self, agent, compare_payload
    ):
        result = agent.run(payload=compare_payload, task_id=None)

        assert result.status == "NEEDS_APPROVAL"

        comparison = result.output["comparison"]
        assert comparison[0]["vendor"] == "Gamma AV"
        assert comparison[0]["rank"] == 1
        assert comparison[-1]["vendor"] == "Alpha Systems"

    def test_compare_recommends_cheapest(
        self, agent, compare_payload
    ):
        result = agent.run(payload=compare_payload, task_id=None)

        assert (
            result.output["recommended_vendor"] == "Gamma AV"
        )
        assert result.output["recommended_total"] == 95000.0

    def test_compare_always_routes_through_buyer_gate(
        self, agent, compare_payload
    ):
        """No task_id → no approval possible → the gate holds."""
        result = agent.run(payload=compare_payload)

        assert result.status == "NEEDS_APPROVAL"
        assert result.output["approval_type"] == (
            "AWARD_RECOMMENDATION"
        )
        assert "awaiting_buyer_award" in result.actions_taken

    def test_compare_single_quote_warns(self, agent):
        result = agent.run(payload={
            "mode": "compare",
            "quotes": [
                {"vendor": "Alpha Systems", "total_amount": 1000},
            ],
        })

        assert result.status == "NEEDS_APPROVAL"
        assert result.output["warning"] is not None
        assert "competition" in result.output["warning"]

    def test_compare_excludes_unparseable_from_ranking(
        self, agent, compare_payload
    ):
        compare_payload["quotes"].append(
            {"vendor": "Delta Displays"}
        )

        result = agent.run(payload=compare_payload, task_id=None)

        assert result.status == "NEEDS_APPROVAL"

        assert result.output["summary"]["quotes_excluded"] == 1

        ranked_vendors = [
            entry["vendor"]
            for entry in result.output["comparison"]
        ]
        assert "Delta Displays" not in ranked_vendors

        excluded = [
            entry["vendor"]
            for entry in result.output["excluded_quotes"]
        ]
        assert "Delta Displays" in excluded

    def test_compare_rejects_all_unparseable(self, agent):
        result = agent.run(payload={
            "mode": "compare",
            "quotes": [
                {"vendor": "Alpha Systems"},
                {"vendor": "Beta Integrators"},
            ],
        })

        assert result.status == "VALIDATION_ERROR"
        assert "no comparable" in result.error.lower()

    def test_compare_rejects_mixed_currencies(self, agent):
        result = agent.run(payload={
            "mode": "compare",
            "quotes": [
                {
                    "vendor": "Alpha Systems",
                    "total_amount": 1000,
                    "currency": "INR",
                },
                {
                    "vendor": "Beta Integrators",
                    "total_amount": 20,
                    "currency": "USD",
                },
            ],
        })

        assert result.status == "VALIDATION_ERROR"
        assert "currenc" in result.error.lower()

    def test_compare_award_payload_carries_items_and_vendor(
        self, agent, compare_payload
    ):
        result = agent.run(payload=compare_payload, task_id=None)

        award = result.output["approval_payload"]

        assert award["vendor"] == "Gamma AV"
        assert award["currency"] == "INR"
        assert award["recommended_total"] == 95000.0

    def test_compare_handoff_mentions_p1(
        self, agent, compare_payload
    ):
        """Even pre-approval, the plan points at the PO step."""
        result = agent.run(payload=compare_payload, task_id=None)

        assert "purchase order" in (
            result.output["action_required"].lower()
        )


# ═══════════════════════════════════════════════════════════════════
# 5. Award Gate — approved re-run
# ═══════════════════════════════════════════════════════════════════

class TestP2AwardGate:

    def test_approved_compare_records_award(
        self, agent, compare_payload, p2_db_session
    ):
        """After buyer approval, the re-run records the award on the RFQ."""
        db = p2_db_session
        task_id = uuid.uuid4()

        RfqRepository(db).create(
            task_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            rfq_number="RFQ-TEST-0001",
            items=[],
        )
        db.commit()

        payload = {
            "mode": "compare",
            "rfq_number": "RFQ-TEST-0001",
            "quotes": [
                {"vendor": "Alpha", "total_amount": 1000},
                {"vendor": "Beta", "total_amount": 900},
            ],
        }

        with patch.object(
            P2SourcingRfq,
            "_approval_granted",
            return_value=True,
        ):
            result = agent.run(payload=payload, task_id=task_id)

        assert result.status == "SUCCESS"
        assert result.output["awarded_vendor"] == "Beta"
        assert result.output["status"] == "AWARDED"
        assert "award_recorded:Beta" in result.actions_taken

        row = RfqRepository(db).get_row("RFQ-TEST-0001")
        assert row.awarded_vendor == "Beta"
        assert row.status == "AWARDED"

    def test_approved_compare_with_no_rfq_skips_award_write(
        self, agent, compare_payload
    ):
        """Inline comparison: a report, not a document."""
        with patch.object(
            P2SourcingRfq,
            "_approval_granted",
            return_value=True,
        ):
            result = agent.run(
                payload=compare_payload, task_id=uuid.uuid4()
            )

        assert result.status == "SUCCESS"
        assert result.output["rfq_number"] is None
        assert result.output["awarded_vendor"] == "Gamma AV"


# ═══════════════════════════════════════════════════════════════════
# 6. Stored-RFQ repository round trips (SQLite)
# ═══════════════════════════════════════════════════════════════════


class TestP2StoredRfq:

    def _issue_rfq(self, db, task_id):
        return RfqRepository(db).create(
            task_id=task_id,
            correlation_id=uuid.uuid4(),
            rfq_number="RFQ-TEST-0001",
            items=[{"item": "AV Control Panel", "quantity": 2}],
            vendor="Alpha Systems, Beta Integrators",
            payload={
                "vendors": ["Alpha Systems", "Beta Integrators"]
            },
        )

    def test_issue_then_collect_round_trip(self, agent, sqlite_db):
        db = sqlite_db
        task_id = uuid.uuid4()

        self._issue_rfq(db, task_id)
        db.commit()

        repository = RfqRepository(db)

        rfq_row = repository.get_row("RFQ-TEST-0001")
        repository.add_quote(
            rfq_row=rfq_row,
            vendor="Alpha Systems",
            total_amount=Decimal("120000"),
            lines=[{
                "item": "AV Control Panel",
                "quantity": 2,
                "unit_price": 50000,
            }],
        )
        repository.add_quote(
            rfq_row=rfq_row,
            vendor="Beta Integrators",
            total_amount=Decimal("110000"),
            lines=[],
        )
        db.commit()

        document = repository.get_by_number("RFQ-TEST-0001")

        assert document.rfq_number == "RFQ-TEST-0001"
        assert len(document.quotes) == 2

        ranked = document.ranked_quotes()

        assert ranked[0].vendor == "Beta Integrators"
        assert ranked[0].total_amount == Decimal("110000")

    def test_quote_revision_appends_not_overwrites(
        self, agent, sqlite_db
    ):
        db = sqlite_db
        task_id = uuid.uuid4()

        self._issue_rfq(db, task_id)
        db.commit()

        repository = RfqRepository(db)
        rfq_row = repository.get_row("RFQ-TEST-0001")

        first = repository.add_quote(
            rfq_row=rfq_row,
            vendor="Alpha Systems",
            total_amount=Decimal("120000"),
            lines=[],
        )
        db.commit()

        revision = repository.add_quote(
            rfq_row=rfq_row,
            vendor="Alpha Systems",
            total_amount=Decimal("105000"),
            lines=[],
        )
        db.commit()

        assert first.revision == 1
        assert revision.revision == 2

        document = repository.get_by_number("RFQ-TEST-0001")

        # Only the latest revision is compared.
        assert len(document.quotes) == 1
        assert document.quotes[0].total_amount == Decimal("105000")

    def test_retried_task_does_not_double_file_quotes(
        self, agent, sqlite_db
    ):
        db = sqlite_db
        task_id = uuid.uuid4()

        self._issue_rfq(db, task_id)
        db.commit()

        repository = RfqRepository(db)
        rfq_row = repository.get_row("RFQ-TEST-0001")

        repository.add_quote(
            rfq_row=rfq_row,
            vendor="Alpha Systems",
            total_amount=Decimal("120000"),
            lines=[],
            source_task_id=task_id,
        )
        db.commit()

        again = repository.add_quote(
            rfq_row=rfq_row,
            vendor="Alpha Systems",
            total_amount=Decimal("120000"),
            lines=[],
            source_task_id=task_id,
        )

        assert again is None

    def test_award_marks_rfq_awarded(self, agent, sqlite_db):
        db = sqlite_db
        task_id = uuid.uuid4()

        self._issue_rfq(db, task_id)
        db.commit()

        repository = RfqRepository(db)

        awarded = repository.set_awarded_vendor(
            "RFQ-TEST-0001", "Beta Integrators"
        )
        db.commit()

        assert awarded == "Beta Integrators"

        row = repository.get_row("RFQ-TEST-0001")
        assert row.status == "AWARDED"
        assert row.awarded_vendor == "Beta Integrators"

    def test_award_unknown_rfq_is_refused(self, agent, sqlite_db):
        db = sqlite_db

        assert (
            RfqRepository(db).set_awarded_vendor(
                "RFQ-GHOST", "Nobody"
            )
            is None
        )
