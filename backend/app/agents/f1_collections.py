import uuid
from datetime import datetime, timezone, timedelta

from app.agents.base import BaseAgent, AgentResult


class F1Collections(BaseAgent):
    """Collections Agent — F1

    Handles collections and follow-up:
    - Calculates aging buckets
    - Generates collection reminders
    - Prioritizes accounts by overdue amount
    - Drafts follow-up communications
    """

    agent_code = "F1"
    agent_name = "Collections"

    def __init__(self):
        super().__init__()

    def describe(self) -> str:
        return (
            "Collections reminders and follow-up on "
            "outstanding payments."
        )

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        invoices = payload.get("invoices")
        if not invoices or not isinstance(invoices, list):
            return "Payload must contain 'invoices' as a non-empty list"

        for i, inv in enumerate(invoices):
            if not inv.get("invoice_number"):
                return f"Invoice {i}: 'invoice_number' is required"
            if not inv.get("customer"):
                return f"Invoice {i}: 'customer' is required"
            amount = inv.get("outstanding_amount")
            if amount is None or amount <= 0:
                return f"Invoice {i}: 'outstanding_amount' must be > 0"

        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        invoices = payload["invoices"]
        follow_up_action = payload.get("follow_up_action", "REMINDER")

        # Aging buckets
        aging = {
            "current": {"count": 0, "total": 0.0},
            "days_1_30": {"count": 0, "total": 0.0},
            "days_31_60": {"count": 0, "total": 0.0},
            "days_61_90": {"count": 0, "total": 0.0},
            "over_90": {"count": 0, "total": 0.0},
        }

        # Process each invoice
        processed = []

        for inv in invoices:
            due_date_str = inv.get("due_date", now.strftime("%Y-%m-%d"))
            try:
                # Naive midnight — compared against now.date(), never
                # against the aware `now` itself.
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
            except (ValueError, TypeError):
                due_date = None

            today = now.date()

            if due_date is not None:
                days_overdue = max(0, (today - due_date.date()).days)
            else:
                days_overdue = 0
            amount = inv["outstanding_amount"]

            # Assign aging bucket
            if days_overdue <= 0:
                bucket = "current"
            elif days_overdue <= 30:
                bucket = "days_1_30"
            elif days_overdue <= 60:
                bucket = "days_31_60"
            elif days_overdue <= 90:
                bucket = "days_61_90"
            else:
                bucket = "over_90"

            aging[bucket]["count"] += 1
            aging[bucket]["total"] += amount

            # Generate reminder message
            if days_overdue > 0:
                message = (
                    f"Payment of ₹{amount:,.2f} for invoice "
                    f"{inv['invoice_number']} is {days_overdue} "
                    f"days overdue. Please arrange immediate payment."
                )
            else:
                message = (
                    f"Reminder: Invoice {inv['invoice_number']} "
                    f"of ₹{amount:,.2f} is due on {due_date_str}. "
                    f"Please ensure timely payment."
                )

            processed.append({
                "invoice_number": inv["invoice_number"],
                "customer": inv["customer"],
                "outstanding_amount": amount,
                "due_date": due_date_str,
                "days_overdue": days_overdue,
                "aging_bucket": bucket,
                "follow_up_action": follow_up_action,
                "message": message,
                "contact_email": inv.get("contact_email"),
                "contact_phone": inv.get("contact_phone"),
            })

        # Sort by days overdue (highest first)
        processed.sort(key=lambda x: x["days_overdue"], reverse=True)

        # Summary
        total_outstanding = sum(
            inv["outstanding_amount"] for inv in invoices
        )
        total_overdue = sum(
            1 for inv in invoices
            if inv.get("due_date", "") < now.strftime("%Y-%m-%d")
        )

        actions = [
            "analyzed_aging",
            f"processed_{len(processed)}_invoices",
        ]

        if follow_up_action == "REMINDER":
            actions.append("generated_reminders")
        elif follow_up_action == "CALL":
            actions.append("flagged_for_calls")
        elif follow_up_action == "LEGAL_NOTICE":
            actions.append("flagged_for_legal")

        output = {
            "processed_invoices": processed,
            "aging_summary": aging,
            "summary": {
                "total_invoices": len(processed),
                "total_outstanding": round(total_outstanding, 2),
                "total_overdue": total_overdue,
                "oldest_overdue_days": max(
                    (p["days_overdue"] for p in processed),
                    default=0,
                ),
                "follow_up_action": follow_up_action,
            },
        }

        self.logger.info(
            "Collections: %d invoices, total outstanding %.2f, "
            "%d overdue",
            len(processed),
            total_outstanding,
            total_overdue,
        )

        return AgentResult(
            status="SUCCESS",
            output=output,
            actions_taken=actions,
        )
