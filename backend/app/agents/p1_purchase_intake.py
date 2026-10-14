import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.agents.base import BaseAgent, AgentResult
from app.services.purchase import PurchaseService
from app.gateways.procurement_base import ProcurementGateway


class P1PurchaseIntake(BaseAgent):
    """P1 — Purchase Intake Agent

    Workflow:
        1. Validate request          (PurchaseService)
        2. Calculate total           (PurchaseService)
        3. Check budget              (PurchaseService)
        4. Policy / approval gate    (PolicyGate — handled by executor)
        5. Create purchase order     (ProcurementGateway)
        6. Record action ledger      (ActionLedger)

    Idempotency:
        - If a ledger entry already exists for this correlation_id,
          return the previous result instead of creating a duplicate PO.
    """

    agent_code = "P1"
    agent_name = "Purchase Intake"

    def __init__(
        self,
        gateway: ProcurementGateway | None = None,
    ):
        super().__init__()
        self.purchase_service = PurchaseService()
        self.gateway = gateway  # injected, defaults to Mock if None

    def describe(self) -> str:
        return (
            "Purchase request intake and PO workflow. "
            "Validates items, checks budgets, creates purchase orders."
        )

    def validate(self, payload: dict) -> str | None:
        """Validate the incoming purchase request payload.

        Returns error message if invalid, None if OK.
        """
        if not payload:
            return "Payload is empty"

        items = payload.get("items")
        if not items or not isinstance(items, list):
            return "Payload must contain 'items' as a non-empty list"

        for i, item in enumerate(items):
            if not item.get("item"):
                return f"Item {i}: 'item' name is required"
            quantity = item.get("quantity")
            if quantity is None or quantity <= 0:
                return f"Item {i}: 'quantity' must be > 0"
            unit_price = item.get("unit_price")
            if unit_price is None or unit_price < 0:
                return f"Item {i}: 'unit_price' must be >= 0"

        if not payload.get("vendor"):
            return "Payload must contain 'vendor'"

        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        # ── Step 1: Validate (already done by base run(), but
        #             PurchaseService adds extra checks) ─────────────
        try:
            self.purchase_service.validate_request(payload)
        except ValueError as exc:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=str(exc),
            )

        # ── Step 2: Calculate total ──────────────────────────────────
        total = self.purchase_service.calculate_total(payload)
        over_budget = self.purchase_service.budget_exceeded(
            payload, total
        )

        # ── Step 3: Budget check → approval gate ─────────────────────
        # If over budget, check if approval already exists.
        # If not, pause and request approval.
        if over_budget:
            approval_exists = self._approval_granted(task_id)

            if not approval_exists:
                self.logger.warning(
                    "Total %s exceeds budget %s — requesting approval",
                    total, payload.get("budget_limit"),
                )
                return AgentResult(
                    status="NEEDS_APPROVAL",
                    output={
                        "total_amount": float(total),
                        "budget_limit": float(
                            payload.get("budget_limit", 0)
                        ),
                        "vendor": payload.get("vendor"),
                        "items": payload.get("items"),
                        "reason": (
                            f"Total {total} exceeds budget "
                            f"{payload.get('budget_limit')}"
                        ),
                    },
                    actions_taken=[
                        "validated_input",
                        "calculated_total",
                        "flagged_over_budget",
                    ],
                )

            self.logger.info(
                "Budget approval already granted for task %s",
                task_id,
            )

        actions = ["validated_input", "calculated_total"]
        if over_budget:
            actions.append("flagged_over_budget")

        # ── Step 4: Create purchase order ────────────────────────────
        po_number = self._generate_po_number(now)

        gateway = self._get_gateway()
        po_result = gateway.create_purchase_order(
            po_number=po_number,
            vendor=payload["vendor"],
            items=payload["items"],
            total=total,
            requested_by=requested_by,
        )

        actions.append(f"created_po:{po_number}")

        output = {
            "po_number": po_number,
            "purchase_id": po_result.purchase_id,
            "vendor": payload["vendor"],
            "total_amount": float(total),
            "currency": payload.get("currency", "INR"),
            "over_budget": over_budget,
            "items": payload["items"],
            "status": po_result.status,
            "created_at": now.isoformat(),
        }

        if over_budget:
            output["budget_approved"] = True
            output["warning"] = (
                f"Total {total} exceeds budget. "
                f"Manager approval was granted."
            )

        self.logger.info(
            "P1: created PO %s — total %s, vendor=%s",
            po_number, total, payload["vendor"],
        )

        return AgentResult(
            status="SUCCESS",
            output=output,
            actions_taken=actions,
        )

    # ── Private helpers ──────────────────────────────────────────────

    def _get_gateway(self) -> ProcurementGateway:
        """Get the procurement gateway — real Zoho when configured, never mock in prod.

        MockProcurementGateway is only for unit tests that inject it explicitly.
        In production this fails fast if Zoho not configured — no silent mock PO.
        """
        if self.gateway is not None:
            return self.gateway

        from app.core.config import settings
        from app.integrations.zoho_books import get_zoho_client

        client = get_zoho_client()
        # If Zoho configured, use real gateway; else fail fast (no mock)
        if client.configured:
            from app.gateways.zoho_procurement import ZohoProcurementGateway
            self.gateway = ZohoProcurementGateway()
            return self.gateway

        # In test/dev without Zoho, allow mock but log clearly
        if settings.app_env in ("test", "development"):
            import logging
            logging.getLogger(__name__).warning(
                "Zoho not configured — P1 using MockProcurementGateway (test/dev only). "
                "Set ZOHO_* in .env and GET /auth/zoho for real Zoho data. "
                "This mock will NOT be used in production."
            )
            from app.gateways.mock_procurement import MockProcurementGateway
            self.gateway = MockProcurementGateway()
            return self.gateway

        raise RuntimeError(
            "Zoho Books not configured — cannot create real PO. "
            "Complete OAuth at GET http://127.0.0.1:8000/auth/zoho . "
            "Missing ZOHO_CLIENT_ID/SECRET/REFRESH_TOKEN/ORG_ID in backend/.env"
        )

    def _generate_po_number(self, now: datetime) -> str:
        """Next PO number from the database sequence.

        A Postgres sequence rather than an in-process counter, so numbers
        survive restarts and never collide between concurrent workers.
        """
        from app.services.numbering import (
            PO_SEQUENCE,
            next_document_number,
        )

        return next_document_number("PO", PO_SEQUENCE, now)


