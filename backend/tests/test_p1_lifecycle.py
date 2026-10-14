"""P1 Agent — Full Lifecycle Test Suite

Tests the complete P1 workflow:
    1. Validation (reject bad payloads)
    2. Calculation (correct totals)
    3. Budget check (flag over-budget)
    4. Gateway interaction (PO creation)
    5. Idempotency (duplicate detection)
    6. End-to-end (happy path)

Run: pytest tests/test_p1_lifecycle.py -v
"""

import uuid
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

# Real imports. External effects are patched per-test:
#   - PO-number generation is stubbed so no Postgres sequence is hit
#   - gateway is a MagicMock that tracks calls
#   - _approval_granted short-circuits on task_id=None without a DB
from app.agents.p1_purchase_intake import (
    P1PurchaseIntake,
)
from app.gateways.procurement_base import (
    PurchaseOrderResult,
)
from app.services.purchase import PurchaseService
from app.services import numbering as numbering_mod


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def isolated_numbering(monkeypatch):
    """Keep execution off the real database sequences."""
    monkeypatch.setattr(numbering_mod, "next_document_number",
                        lambda *a, **k: "PO-TEST-0001")
    yield

@pytest.fixture
def mock_gateway():
    """Mock procurement gateway that tracks calls."""
    gw = MagicMock()
    gw.create_purchase_order.return_value = PurchaseOrderResult(
        purchase_id="PO-202608-00001",
        vendor="Acme Corp",
        total=Decimal("1500.00"),
        status="CREATED",
        items=[],
    )
    gw.get_purchase_order.return_value = None
    return gw


@pytest.fixture
def agent(mock_gateway):
    """P1 agent with mock gateway."""
    return P1PurchaseIntake(gateway=mock_gateway)


@pytest.fixture
def valid_payload():
    """Standard valid purchase request."""
    return {
        "vendor": "Acme Corp",
        "items": [
            {
                "item": "Widget A",
                "quantity": 10,
                "unit_price": 100.00,
            },
            {
                "item": "Widget B",
                "quantity": 5,
                "unit_price": 200.00,
            },
        ],
        "currency": "INR",
        "budget_limit": 5000,
    }


@pytest.fixture
def over_budget_payload():
    """Purchase request that exceeds budget."""
    return {
        "vendor": "Expensive Corp",
        "items": [
            {
                "item": "Premium Widget",
                "quantity": 100,
                "unit_price": 500.00,
            },
        ],
        "budget_limit": 10000,
    }


# ═══════════════════════════════════════════════════════════════════
# 1. Validation Tests
# ═══════════════════════════════════════════════════════════════════

class TestP1Validation:
    """Step 1: Validate request payload."""

    def test_empty_payload_fails(self, agent):
        result = agent.validate({})
        assert result is not None
        assert "empty" in result.lower()

    def test_missing_items_fails(self, agent):
        result = agent.validate({"vendor": "Acme"})
        assert result is not None
        assert "items" in result

    def test_empty_items_list_fails(self, agent):
        result = agent.validate({"vendor": "Acme", "items": []})
        assert result is not None

    def test_missing_item_name_fails(self, agent):
        payload = {
            "vendor": "Acme",
            "items": [{"quantity": 1, "unit_price": 10}],
        }
        result = agent.validate(payload)
        assert result is not None
        assert "item" in result.lower()

    def test_zero_quantity_fails(self, agent):
        payload = {
            "vendor": "Acme",
            "items": [{"item": "Widget", "quantity": 0, "unit_price": 10}],
        }
        result = agent.validate(payload)
        assert result is not None
        assert "quantity" in result

    def test_negative_unit_price_fails(self, agent):
        payload = {
            "vendor": "Acme",
            "items": [
                {"item": "Widget", "quantity": 1, "unit_price": -5}
            ],
        }
        result = agent.validate(payload)
        assert result is not None
        assert "unit_price" in result

    def test_missing_vendor_fails(self, agent):
        payload = {
            "items": [{"item": "Widget", "quantity": 1, "unit_price": 10}],
        }
        result = agent.validate(payload)
        assert result is not None
        assert "vendor" in result

    def test_valid_payload_passes(self, agent, valid_payload):
        result = agent.validate(valid_payload)
        assert result is None

    def test_purchase_service_validation(self):
        """PurchaseService adds extra validation."""
        ps = PurchaseService()

        with pytest.raises(ValueError, match="at least one item"):
            ps.validate_request({"items": []})

        with pytest.raises(ValueError, match="missing 'item'"):
            ps.validate_request({
                "items": [{"quantity": 1, "unit_price": 10}]
            })


# ═══════════════════════════════════════════════════════════════════
# 2. Calculation Tests
# ═══════════════════════════════════════════════════════════════════

class TestP1Calculation:
    """Step 2: Calculate total amount."""

    def test_single_item_total(self):
        ps = PurchaseService()
        payload = {
            "items": [{"item": "A", "quantity": 3, "unit_price": 100}],
        }
        total = ps.calculate_total(payload)
        assert total == Decimal("300")

    def test_multiple_items_total(self):
        ps = PurchaseService()
        payload = {
            "items": [
                {"item": "A", "quantity": 2, "unit_price": 100},
                {"item": "B", "quantity": 3, "unit_price": 50},
            ],
        }
        total = ps.calculate_total(payload)
        assert total == Decimal("350")

    def test_decimal_precision(self):
        ps = PurchaseService()
        payload = {
            "items": [
                {"item": "A", "quantity": 3, "unit_price": 33.33},
            ],
        }
        total = ps.calculate_total(payload)
        assert total == Decimal("99.99")

    def test_budget_exceeded_true(self):
        ps = PurchaseService()
        payload = {
            "items": [{"item": "A", "quantity": 10, "unit_price": 100}],
            "budget_limit": 500,
        }
        total = ps.calculate_total(payload)
        assert ps.budget_exceeded(payload, total) is True

    def test_budget_exceeded_false(self):
        ps = PurchaseService()
        payload = {
            "items": [{"item": "A", "quantity": 1, "unit_price": 100}],
            "budget_limit": 500,
        }
        total = ps.calculate_total(payload)
        assert ps.budget_exceeded(payload, total) is False

    def test_no_budget_limit(self):
        ps = PurchaseService()
        payload = {
            "items": [{"item": "A", "quantity": 100, "unit_price": 100}],
        }
        total = ps.calculate_total(payload)
        assert ps.budget_exceeded(payload, total) is False


# ═══════════════════════════════════════════════════════════════════
# 3. Gateway Interaction Tests
# ═══════════════════════════════════════════════════════════════════

class TestP1Gateway:
    """Step 5: Create purchase order via gateway."""

    def test_gateway_called_with_correct_args(
        self, agent, mock_gateway, valid_payload
    ):
        result = agent.run(payload=valid_payload)

        assert result.status == "SUCCESS"
        mock_gateway.create_purchase_order.assert_called_once()

        call_kwargs = mock_gateway.create_purchase_order.call_args[1]
        assert call_kwargs["vendor"] == "Acme Corp"
        assert call_kwargs["total"] == Decimal("2000")
        assert len(call_kwargs["items"]) == 2

    def test_po_number_in_output(self, agent, valid_payload):
        result = agent.run(payload=valid_payload)

        assert "po_number" in result.output
        assert result.output["po_number"].startswith("PO-")

    def test_po_status_in_output(self, agent, valid_payload):
        result = agent.run(payload=valid_payload)

        assert result.output["status"] == "CREATED"

    def test_agent_code_is_p1(self, agent):
        assert agent.agent_code == "P1"


# ═══════════════════════════════════════════════════════════════════
# 4. Budget Flag Tests
# ═══════════════════════════════════════════════════════════════════

class TestP1BudgetCheck:
    """Step 3: Budget threshold check."""

    def test_under_budget_no_warning(
        self, agent, mock_gateway, valid_payload
    ):
        result = agent.run(payload=valid_payload)

        assert result.output["over_budget"] is False
        assert "warning" not in result.output

    def test_over_budget_flags_warning(
        self, agent, mock_gateway, over_budget_payload
    ):
        result = agent.run(payload=over_budget_payload)

        assert result.status == "NEEDS_APPROVAL"
        assert result.output["total_amount"] == 50000.0
        assert result.output["budget_limit"] == 10000.0
        assert "exceeds budget" in result.output["reason"].lower()

    def test_over_budget_action_recorded(
        self, agent, mock_gateway, over_budget_payload
    ):
        result = agent.run(payload=over_budget_payload)

        assert "flagged_over_budget" in result.actions_taken


# ═══════════════════════════════════════════════════════════════════
# 5. Actions Taken Tests
# ═══════════════════════════════════════════════════════════════════

class TestP1ActionsTaken:
    """Verify correct actions are recorded."""

    def test_success_records_actions(
        self, agent, mock_gateway, valid_payload
    ):
        result = agent.run(payload=valid_payload)

        assert "validated_input" in result.actions_taken
        assert "calculated_total" in result.actions_taken
        assert any(
            a.startswith("created_po:") for a in result.actions_taken
        )

    def test_validation_error_records_no_actions(self, agent):
        result = agent.run(payload={})

        assert result.status == "VALIDATION_ERROR"
        assert len(result.actions_taken) == 0


# ═══════════════════════════════════════════════════════════════════
# 6. Idempotency Tests
# ═══════════════════════════════════════════════════════════════════

class TestP1Idempotency:
    """Duplicate detection via correlation_id."""

    def test_first_execution_creates_ledger(self, agent, valid_payload):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = (
            None
        )

        correlation_id = uuid.uuid4()
        result = agent.execute_with_idempotency(
            db=db,
            payload=valid_payload,
            correlation_id=correlation_id,
        )

        assert result.status == "SUCCESS"
        db.add.assert_called_once()
        db.commit.assert_called()

    def test_second_execution_returns_cached(
        self, agent, valid_payload
    ):
        db = MagicMock()
        existing_ledger = MagicMock()
        existing_ledger.output = {
            "po_number": "PO-202608-00001",
            "total_amount": 1500.00,
        }
        db.query.return_value.filter.return_value.first.return_value = (
            existing_ledger
        )

        correlation_id = uuid.uuid4()
        result = agent.execute_with_idempotency(
            db=db,
            payload=valid_payload,
            correlation_id=correlation_id,
        )

        assert result.status == "SUCCESS"
        assert "idempotent_return" in result.actions_taken
        # Should NOT create a new PO
        assert "created_po:" not in str(result.actions_taken)

    def test_no_correlation_id_skips_idempotency(
        self, agent, mock_gateway, valid_payload
    ):
        result = agent.execute_with_idempotency(
            db=MagicMock(),
            payload=valid_payload,
            correlation_id=None,
        )

        assert result.status == "SUCCESS"
        assert "idempotent_return" not in result.actions_taken


# ═══════════════════════════════════════════════════════════════════
# 7. End-to-End Lifecycle Test
# ═══════════════════════════════════════════════════════════════════

class TestP1EndToEnd:
    """Full happy-path lifecycle."""

    def test_complete_lifecycle(
        self, agent, mock_gateway, valid_payload
    ):
        """Validate → Calculate → Budget → Gateway → Result."""
        result = agent.run(
            payload=valid_payload,
            task_id=uuid.uuid4(),
            requested_by="test_user",
        )

        # Step 1: Passed validation
        assert result.status == "SUCCESS"

        # Step 2: Correct total (10*100 + 5*200 = 2000)
        assert result.output["total_amount"] == 2000.00

        # Step 3: Budget check
        assert result.output["over_budget"] is False

        # Step 4: Policy gate (handled by executor — not tested here)

        # Step 5: PO created
        assert result.output["po_number"].startswith("PO-")
        assert result.output["vendor"] == "Acme Corp"
        assert result.output["status"] == "CREATED"

        # Step 6: Actions recorded
        assert "validated_input" in result.actions_taken
        assert "calculated_total" in result.actions_taken
        assert any(
            a.startswith("created_po:") for a in result.actions_taken
        )

    def test_full_lifecycle_with_over_budget(
        self, agent, mock_gateway, over_budget_payload
    ):
        """Full lifecycle with budget exceeded → NEEDS_APPROVAL."""
        result = agent.run(payload=over_budget_payload)

        assert result.status == "NEEDS_APPROVAL"
        assert "flagged_over_budget" in result.actions_taken
        assert "reason" in result.output


# ═══════════════════════════════════════════════════════════════════
# 8. Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestP1EdgeCases:
    """Boundary conditions and edge cases."""

    def test_single_item_no_optional_fields(self, agent, mock_gateway):
        payload = {
            "vendor": "Minimal Corp",
            "items": [{"item": "Thing", "quantity": 1, "unit_price": 50}],
        }
        result = agent.run(payload=payload)

        assert result.status == "SUCCESS"
        assert result.output["currency"] == "INR"  # default

    def test_item_with_zero_unit_price(self, agent, mock_gateway):
        payload = {
            "vendor": "Free Corp",
            "items": [{"item": "Freebie", "quantity": 5, "unit_price": 0}],
        }
        result = agent.run(payload=payload)

        assert result.status == "SUCCESS"
        assert result.output["total_amount"] == 0.0

    def test_large_quantity(self, agent, mock_gateway):
        payload = {
            "vendor": "Bulk Corp",
            "items": [
                {"item": "Pin", "quantity": 1000000, "unit_price": 0.01}
            ],
        }
        result = agent.run(payload=payload)

        assert result.status == "SUCCESS"
        assert result.output["total_amount"] == 10000.0
