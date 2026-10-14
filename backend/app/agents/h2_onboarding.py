"""H2 Onboarding — employee onboarding checklist management.

Autonomy: A2 (supervised). Checklist creation is automatic; item completion
requires the item owner to confirm via HITL approval.

Actions:
  start    — create OnboardingChecklist rows for a new hire
  update   — mark a checklist item as complete
  complete — assert all items done and flip employee onboarding_status → done
  report   — show checklist progress for an employee
"""

import uuid
from datetime import date, datetime, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"start", "update", "complete", "report"}

DEFAULT_ITEMS = [
    {"item": "Issue laptop & accessories", "owner": "IT", "due_offset_days": 1},
    {"item": "Create AD / email account", "owner": "IT", "due_offset_days": 1},
    {"item": "Tally access granted", "owner": "Finance", "due_offset_days": 2},
    {"item": "Zoho CRM access granted", "owner": "IT", "due_offset_days": 2},
    {"item": "ID card issued", "owner": "Admin", "due_offset_days": 3},
    {"item": "Joining kit handed over", "owner": "HR", "due_offset_days": 1},
    {"item": "Policy documents signed", "owner": "HR", "due_offset_days": 5},
    {"item": "Payroll details collected", "owner": "Finance", "due_offset_days": 5},
    {"item": "Reporting manager introduced", "owner": "HR", "due_offset_days": 1},
    {"item": "30-day check-in scheduled", "owner": "HR", "due_offset_days": 7},
]


class H2Onboarding(BaseAgent):
    """Onboarding Agent — H2

    Manages the new-hire checklist:
    - Creates default item set on start (customisable)
    - Tracks completion per item with owner and due date
    - Blocks 'complete' if any item is still open
    - A2: checklist creation is automatic; marking items done requires owner confirmation
    """

    agent_code = "H2"
    agent_name = "Onboarding"

    def describe(self) -> str:
        return "New-hire onboarding checklist creation, tracking, and completion."

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"
        action = payload.get("action")
        if not action:
            return "'action' is required"
        if action not in VALID_ACTIONS:
            return f"action must be one of {sorted(VALID_ACTIONS)}"
        if action in {"start", "update", "complete", "report"}:
            if not payload.get("employee_id"):
                return "'employee_id' is required"
        if action == "update":
            if not payload.get("item"):
                return "'item' (item name) is required for update"
        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        from app.core.db import SessionLocal
        db = SessionLocal()
        try:
            return self._run(db, payload)
        finally:
            db.close()

    def _run(self, db, payload: dict) -> AgentResult:
        from app.models.enterprise import HrEmployee, OnboardingChecklist

        action = payload["action"]
        employee_id = payload["employee_id"]
        now = datetime.now(timezone.utc)

        employee = db.query(HrEmployee).filter(HrEmployee.employee_id == employee_id).first()
        if employee is None:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Employee '{employee_id}' not found in HrEmployee registry",
            )

        if action == "start":
            existing = (
                db.query(OnboardingChecklist)
                .filter(OnboardingChecklist.employee_id == employee_id)
                .count()
            )
            if existing > 0:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error=f"Onboarding already started for employee '{employee_id}' ({existing} items exist)",
                )

            join_date = employee.join_date or date.today()
            custom_items = payload.get("custom_items", [])
            items_to_create = list(DEFAULT_ITEMS) + [
                {"item": ci["item"], "owner": ci.get("owner", "HR"), "due_offset_days": ci.get("due_offset_days", 7)}
                for ci in custom_items
            ]

            created = []
            from datetime import timedelta
            for spec in items_to_create:
                due = join_date + timedelta(days=spec["due_offset_days"])
                row = OnboardingChecklist(
                    employee_id=employee_id,
                    item=spec["item"],
                    due_date=due,
                    owner=spec["owner"],
                    completed=False,
                )
                db.add(row)
                created.append({"item": spec["item"], "due_date": str(due), "owner": spec["owner"]})

            employee.onboarding_status = "in_progress"
            db.commit()

            return AgentResult(
                status="SUCCESS",
                output={
                    "employee_id": employee_id,
                    "employee_name": employee.name,
                    "join_date": str(join_date),
                    "items_created": len(created),
                    "checklist": created,
                    "next_step": "Item owners will be notified to complete their tasks.",
                },
                actions_taken=["checklist_created", f"onboarding_status:in_progress"],
            )

        if action == "update":
            item_name = payload["item"]
            item = (
                db.query(OnboardingChecklist)
                .filter(
                    OnboardingChecklist.employee_id == employee_id,
                    OnboardingChecklist.item == item_name,
                )
                .first()
            )
            if item is None:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error=f"Checklist item '{item_name}' not found for employee '{employee_id}'",
                )
            if item.completed:
                return AgentResult(
                    status="SUCCESS",
                    output={"message": f"'{item_name}' was already marked complete.", "completed_ts": str(item.completed_ts)},
                    actions_taken=[],
                )
            item.completed = True
            item.completed_ts = now
            if payload.get("completed_by"):
                item.owner = payload["completed_by"]
            db.commit()

            return AgentResult(
                status="SUCCESS",
                output={
                    "employee_id": employee_id,
                    "item": item_name,
                    "completed": True,
                    "completed_ts": now.isoformat(),
                },
                actions_taken=["item_completed"],
            )

        if action == "complete":
            items = (
                db.query(OnboardingChecklist)
                .filter(OnboardingChecklist.employee_id == employee_id)
                .all()
            )
            if not items:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error=f"No checklist items found for '{employee_id}'. Run action=start first.",
                )
            open_items = [i.item for i in items if not i.completed]
            if open_items:
                return AgentResult(
                    status="NEEDS_APPROVAL",
                    output={
                        "employee_id": employee_id,
                        "open_items": open_items,
                        "message": f"{len(open_items)} item(s) still open — cannot mark onboarding complete.",
                    },
                    actions_taken=[],
                )
            employee.onboarding_status = "done"
            db.commit()
            return AgentResult(
                status="SUCCESS",
                output={
                    "employee_id": employee_id,
                    "employee_name": employee.name,
                    "onboarding_status": "done",
                    "total_items": len(items),
                },
                actions_taken=["onboarding_completed"],
            )

        if action == "report":
            items = (
                db.query(OnboardingChecklist)
                .filter(OnboardingChecklist.employee_id == employee_id)
                .order_by(OnboardingChecklist.due_date)
                .all()
            )
            today = date.today()
            return AgentResult(
                status="SUCCESS",
                output={
                    "employee_id": employee_id,
                    "employee_name": employee.name,
                    "onboarding_status": employee.onboarding_status,
                    "total": len(items),
                    "completed": sum(1 for i in items if i.completed),
                    "open": sum(1 for i in items if not i.completed),
                    "overdue": sum(1 for i in items if not i.completed and i.due_date and i.due_date < today),
                    "items": [
                        {
                            "item": i.item,
                            "owner": i.owner,
                            "due_date": str(i.due_date) if i.due_date else None,
                            "completed": i.completed,
                            "completed_ts": i.completed_ts.isoformat() if i.completed_ts else None,
                            "overdue": not i.completed and bool(i.due_date and i.due_date < today),
                        }
                        for i in items
                    ],
                },
                actions_taken=["onboarding_report_generated"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
