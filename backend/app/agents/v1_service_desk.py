import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent, AgentResult


class V1ServiceDesk(BaseAgent):
    """Service Desk Agent — V1

    Handles service requests and RMA:
    - Creates service tickets
    - Validates RMA eligibility
    - Generates ticket numbers
    - Determines priority and SLA
    """

    agent_code = "V1"
    agent_name = "Service Desk"

    _ticket_counter = 7000

    def __init__(self):
        super().__init__()

    SUPPORTED_TYPES = {
        "service_request",
        "rma",
        "warranty_claim",
        "technical_support",
        "complaint",
    }

    PRIORITY_SLA = {
        "CRITICAL": {"response_hours": 2, "resolution_hours": 24},
        "HIGH": {"response_hours": 4, "resolution_hours": 48},
        "MEDIUM": {"response_hours": 8, "resolution_hours": 72},
        "LOW": {"response_hours": 24, "resolution_hours": 120},
    }

    def describe(self) -> str:
        return (
            "Service request and RMA handling. "
            "Creates tickets, validates eligibility, manages SLAs."
        )

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        request_type = payload.get("request_type")
        if not request_type:
            return "'request_type' is required"

        if request_type not in self.SUPPORTED_TYPES:
            return (
                f"Unknown request_type: '{request_type}'. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_TYPES))}"
            )

        if not payload.get("description"):
            return "'description' is required"

        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        V1ServiceDesk._ticket_counter += 1
        ticket_number = f"TKT-{now.strftime('%Y%m')}-{V1ServiceDesk._ticket_counter:05d}"

        request_type = payload["request_type"]
        description = payload["description"]
        priority = payload.get("priority", "MEDIUM")
        customer = payload.get("customer", requested_by or "Unknown")

        # Get SLA
        sla = self.PRIORITY_SLA.get(
            priority, self.PRIORITY_SLA["MEDIUM"]
        )

        # RMA-specific validation
        rma_details = None
        if request_type == "rma":
            rma_details = self._process_rma(payload)

        # Build ticket
        ticket = {
            "ticket_number": ticket_number,
            "request_type": request_type,
            "status": "OPEN",
            "priority": priority,
            "customer": customer,
            "description": description,
            "sla": sla,
            "assigned_to": None,
            "product": payload.get("product"),
            "serial_number": payload.get("serial_number"),
            "purchase_date": payload.get("purchase_date"),
            "warranty_status": payload.get(
                "warranty_status", "UNKNOWN"
            ),
            "rma_details": rma_details,
            "attachments": payload.get("attachments", []),
            "created_at": now.isoformat(),
            "sla_deadline": (
                now.timestamp() + sla["response_hours"] * 3600
            ),
        }

        actions = [
            f"created_ticket:{ticket_number}",
            f"assigned_priority:{priority}",
        ]

        if rma_details:
            actions.append(f"validated_rma:{rma_details['rma_status']}")

        output = {
            "ticket": ticket,
            "summary": {
                "ticket_number": ticket_number,
                "request_type": request_type,
                "priority": priority,
                "sla_response_hours": sla["response_hours"],
                "sla_resolution_hours": sla["resolution_hours"],
            },
        }

        if rma_details and rma_details.get("warning"):
            output["warning"] = rma_details["warning"]

        self.logger.info(
            "Service desk ticket %s created: %s (%s)",
            ticket_number,
            request_type,
            priority,
        )

        return AgentResult(
            status="SUCCESS",
            output=output,
            actions_taken=actions,
        )

    def _process_rma(self, payload: dict) -> dict:
        """Validate RMA eligibility and process."""
        purchase_date = payload.get("purchase_date")
        warranty_status = payload.get("warranty_status", "UNKNOWN")

        rma_status = "ELIGIBLE"
        warning = None

        if warranty_status == "EXPIRED":
            rma_status = "OUT_OF_WARRANTY"
            warning = (
                "Product is out of warranty. "
                "RMA may incur charges."
            )
        elif warranty_status == "UNKNOWN":
            rma_status = "PENDING_VERIFICATION"
            warning = (
                "Warranty status unknown. "
                "Please verify purchase date."
            )

        return {
            "rma_status": rma_status,
            "warranty_status": warranty_status,
            "return_shipping": (
                "PREPAID" if rma_status == "ELIGIBLE"
                else "CUSTOMER_PAID"
            ),
            "replacement_offered": rma_status == "ELIGIBLE",
            "warning": warning,
        }
