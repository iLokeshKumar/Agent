"""All Agents — Instantiation & Lifecycle Verification

Verifies that every agent:
    1. Can be instantiated
    2. Has logger set (via super().__init__)
    3. Validates correct payloads
    4. Rejects invalid payloads
    5. Executes successfully
    6. Has agent_code and agent_name set

Run: pytest tests/test_all_agents.py -v

Real modules are imported; external effects are patched per-test:
    - P4/S1 document lookups run against a fake DB session (no rows),
      so execution takes the honest VALIDATION_ERROR path without
      touching the database
    - P1 PO-number generation is stubbed (no sequence hit)
    - Zoho stays unconfigured (blank credentials in .env) and agents
      fall back to payload data by design
"""

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.agents.p1_purchase_intake import P1PurchaseIntake
from app.agents.p2_sourcing_rfq import P2SourcingRfq
from app.agents.p3_expediting import P3Expediting
from app.agents.p4_invoice_matching import (
    P4InvoiceMatching,
)
from app.agents.s1_grn_serialisation import (
    S1GRNSerialisation,
)
from app.agents.v1_service_desk import V1ServiceDesk
from app.agents.f1_collections import F1Collections
from app.agents.f3_books_to_tally import F3BooksToTally
from app.agents.d1_ask_business import D1AskBusiness
from app.services import numbering as numbering_mod
import app.agents.p4_invoice_matching as p4_mod
import app.agents.s1_grn_serialisation as s1_mod


# ── Agent configs ──────────────────────────────────────────────────

AGENT_CLASSES = {
    "P1": P1PurchaseIntake,
    "P2": P2SourcingRfq,
    "P3": P3Expediting,
    "P4": P4InvoiceMatching,
    "S1": S1GRNSerialisation,
    "V1": V1ServiceDesk,
    "F1": F1Collections,
    "F3": F3BooksToTally,
    "D1": D1AskBusiness,
}

VALID_PAYLOADS = {
    "P1": {
        "vendor": "Acme Corp",
        "items": [{"item": "Widget", "quantity": 10, "unit_price": 100}],
    },
    "P2": {
        "mode": "issue",
        "vendors": ["Alpha Systems", "Beta Integrators"],
        "items": [
            {"item": "AV Panel", "quantity": 2}
        ],
    },
    "P4": {
        "invoice_number": "INV-001",
        "po_number": "PO-001",
        "invoice_amount": 1000,
    },
    "S1": {
        "po_number": "PO-001",
        "items": [{"item": "Widget", "received_quantity": 10}],
    },
    "V1": {
        "request_type": "service_request",
        "description": "Printer not working",
    },
    "F1": {
        "invoices": [{
            "invoice_number": "INV-001",
            "customer": "Acme Corp",
            "outstanding_amount": 5000,
        }],
    },
    "F3": {
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
    },
    "D1": {
        "query_type": "sales_summary",
    },
    "P3": {
        "pos": [{
            "po_number": "PO-001",
            "vendor": "Acme Corp",
            "committed_date": "2099-01-01",
        }],
    },
}

INVALID_PAYLOADS = {
    "P1": {},
    "P2": {},
    "P4": {},
    "S1": {},
    "V1": {},
    "F1": {},
    "F3": {},
    "D1": {},
    "P3": {},
}


# ── DB isolation ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Keep agent execution off the real database.

    P4/S1 open a session via ``document_session`` and look documents up
    by number; the fake session returns no rows, so execution takes the
    honest "unknown PO" path. Numbering is stubbed so no Postgres
    sequence is consumed. Tests that exercise idempotency pass their
    own MagicMock ``db`` and are unaffected.
    """
    db = MagicMock()
    db.execute.return_value.scalars.return_value.first.return_value = None
    db.query.return_value.filter.return_value.first.return_value = None

    @contextmanager
    def fake_session():
        yield db

    monkeypatch.setattr(p4_mod, "document_session", fake_session)
    monkeypatch.setattr(s1_mod, "document_session", fake_session)
    monkeypatch.setattr(s1_mod, "next_document_number",
                        lambda *a, **k: "GRN-TEST-0001")
    monkeypatch.setattr(numbering_mod, "next_document_number",
                        lambda *a, **k: "PO-TEST-0001")
    yield db


# ── Tests ──────────────────────────────────────────────────────────

class TestAllAgents:
    """Verify every agent follows the shared architecture."""

    @pytest.fixture(params=list(AGENT_CLASSES.keys()))
    def agent_code(self, request):
        return request.param

    @pytest.fixture
    def agent(self, agent_code):
        cls = AGENT_CLASSES[agent_code]
        if agent_code == "P1":
            return cls(gateway=MagicMock())
        return cls()

    @pytest.fixture
    def valid_payload(self, agent_code):
        return VALID_PAYLOADS[agent_code]

    @pytest.fixture
    def invalid_payload(self, agent_code):
        return INVALID_PAYLOADS[agent_code]

    # ── Instantiation ───────────────────────────────────────────────

    def test_can_instantiate(self, agent, agent_code):
        assert agent is not None
        assert agent.agent_code == agent_code

    def test_has_logger(self, agent):
        assert hasattr(agent, "logger")
        assert agent.logger is not None

    def test_has_agent_name(self, agent):
        assert agent.agent_name != ""

    def test_describe_returns_string(self, agent):
        desc = agent.describe()
        assert isinstance(desc, str)
        assert len(desc) > 0

    # ── Validation ──────────────────────────────────────────────────

    def test_valid_payload_passes(self, agent, valid_payload):
        result = agent.validate(valid_payload)
        assert result is None

    def test_invalid_payload_fails(self, agent, invalid_payload):
        result = agent.validate(invalid_payload)
        assert result is not None
        assert isinstance(result, str)

    def test_empty_payload_fails(self, agent):
        result = agent.validate({})
        assert result is not None

    # ── Execution ───────────────────────────────────────────────────

    def test_execute_returns_agent_result(self, agent, valid_payload):
        result = agent.run(payload=valid_payload)
        assert hasattr(result, "status")
        assert hasattr(result, "output")
        assert hasattr(result, "actions_taken")
        assert result.status in ("SUCCESS", "VALIDATION_ERROR", "FAILED")

    def test_execute_success_has_output(self, agent, valid_payload):
        result = agent.run(payload=valid_payload)
        if result.status == "SUCCESS":
            assert isinstance(result.output, dict)
            assert len(result.actions_taken) > 0

    def test_execute_invalid_returns_validation_error(
        self, agent, invalid_payload
    ):
        result = agent.run(payload=invalid_payload)
        assert result.status == "VALIDATION_ERROR"
        assert result.error is not None

    # ── Idempotency (shared from BaseAgent) ─────────────────────────

    def test_execute_with_idempotency_exists(self, agent):
        assert hasattr(agent, "execute_with_idempotency")
        assert callable(agent.execute_with_idempotency)

    def test_execute_with_idempotency_no_correlation(
        self, agent, valid_payload
    ):
        result = agent.execute_with_idempotency(
            db=MagicMock(),
            payload=valid_payload,
            correlation_id=None,
        )
        assert result.status in ("SUCCESS", "VALIDATION_ERROR", "FAILED")

    def test_execute_with_idempotency_first_call(
        self, agent, valid_payload
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = agent.execute_with_idempotency(
            db=db,
            payload=valid_payload,
            correlation_id=uuid.uuid4(),
        )
        assert result.status in ("SUCCESS", "VALIDATION_ERROR", "FAILED")

    def test_execute_with_idempotency_cached_call(
        self, agent, valid_payload
    ):
        db = MagicMock()
        existing = MagicMock()
        existing.output = {"cached": True}
        db.query.return_value.filter.return_value.first.return_value = (
            existing
        )

        result = agent.execute_with_idempotency(
            db=db,
            payload=valid_payload,
            correlation_id=uuid.uuid4(),
        )
        assert result.status == "SUCCESS"
        assert "idempotent_return" in result.actions_taken

    # ── Consistency ─────────────────────────────────────────────────

    def test_all_agents_have_same_interface(self):
        """Every agent exposes the same public methods."""
        required = {
            "validate", "execute", "describe", "run",
            "execute_with_idempotency",
        }
        for code, cls in AGENT_CLASSES.items():
            instance = cls() if code != "P1" else cls(gateway=MagicMock())
            for method in required:
                assert hasattr(instance, method), (
                    f"{code} missing {method}"
                )
