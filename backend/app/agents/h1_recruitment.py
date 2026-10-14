"""H1 Recruitment — job intake, JD drafting, pipeline tracking.

Autonomy: A1 (propose only). All actions require HR Manager approval.

Actions:
  intake   — open a new job position (creates JobOpening row)
  update   — change status of an existing opening
  report   — pipeline summary with days-open and fill-rate KPI
"""

import uuid
from datetime import date, datetime, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"intake", "update", "report"}
VALID_STATUSES = {"open", "filled", "cancelled", "on_hold"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}


class H1Recruitment(BaseAgent):
    """Recruitment Agent — H1

    Manages the hiring pipeline:
    - Opens job positions from approved requisitions
    - Drafts JD summaries for recruiter review
    - Tracks time-to-fill against 30-day target
    - Always A1: proposes, never publishes or confirms autonomously
    """

    agent_code = "H1"
    agent_name = "Recruitment"

    def describe(self) -> str:
        return "Job intake, JD drafting, and recruitment pipeline tracking."

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"
        action = payload.get("action")
        if not action:
            return "'action' is required"
        if action not in VALID_ACTIONS:
            return f"action must be one of {sorted(VALID_ACTIONS)}"
        if action == "intake":
            if not payload.get("title"):
                return "'title' is required for intake"
            entity = payload.get("entity", "YEXIS-EL")
            if entity not in VALID_ENTITIES:
                return f"entity must be one of {sorted(VALID_ENTITIES)}"
        if action == "update":
            if not payload.get("opening_id"):
                return "'opening_id' is required for update"
            new_status = payload.get("status")
            if new_status and new_status not in VALID_STATUSES:
                return f"status must be one of {sorted(VALID_STATUSES)}"
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
            return self._run(db, payload, requested_by)
        finally:
            db.close()

    def _run(self, db, payload: dict, requested_by: str | None) -> AgentResult:
        from app.models.enterprise import JobOpening

        action = payload["action"]
        now = datetime.now(timezone.utc)

        if action == "intake":
            target_str = payload.get("target_fill_date")
            target_date = None
            if target_str:
                try:
                    target_date = date.fromisoformat(target_str)
                except ValueError:
                    return AgentResult(
                        status="VALIDATION_ERROR",
                        error=f"target_fill_date '{target_str}' is not a valid YYYY-MM-DD date",
                    )

            opening = JobOpening(
                title=payload["title"],
                department=payload.get("department"),
                entity=payload.get("entity", "YEXIS-EL"),
                requisition_ref=payload.get("requisition_ref"),
                target_fill_date=target_date,
                notes=payload.get("notes"),
                status="open",
            )
            db.add(opening)
            db.commit()
            db.refresh(opening)

            jd_draft = self._draft_jd(payload)

            return AgentResult(
                status="SUCCESS",
                output={
                    "opening_id": str(opening.id),
                    "title": opening.title,
                    "department": opening.department,
                    "entity": opening.entity,
                    "target_fill_date": str(opening.target_fill_date) if opening.target_fill_date else None,
                    "status": opening.status,
                    "jd_draft": jd_draft,
                    "next_step": "HR Manager to review JD draft and publish on job portals.",
                },
                actions_taken=["opening_created", "jd_drafted"],
            )

        if action == "update":
            opening = db.get(JobOpening, payload["opening_id"])
            if opening is None:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error=f"JobOpening '{payload['opening_id']}' not found",
                )
            new_status = payload.get("status")
            if new_status:
                opening.status = new_status
                if new_status == "filled":
                    opening.filled_ts = now
            if payload.get("notes"):
                opening.notes = payload["notes"]
            db.commit()

            return AgentResult(
                status="SUCCESS",
                output={
                    "opening_id": str(opening.id),
                    "title": opening.title,
                    "status": opening.status,
                    "filled_ts": opening.filled_ts.isoformat() if opening.filled_ts else None,
                },
                actions_taken=[f"status_updated:{new_status}" if new_status else "opening_updated"],
            )

        if action == "report":
            entity = payload.get("entity", "YEXIS-EL")
            openings = (
                db.query(JobOpening)
                .filter(JobOpening.entity == entity)
                .all()
            )
            today = date.today()
            pipeline = []
            for o in openings:
                days_open = (today - o.opened_ts.date()).days if o.opened_ts else 0
                pipeline.append({
                    "opening_id": str(o.id),
                    "title": o.title,
                    "department": o.department,
                    "status": o.status,
                    "days_open": days_open,
                    "target_fill_date": str(o.target_fill_date) if o.target_fill_date else None,
                    "overdue": bool(o.target_fill_date and o.status == "open" and o.target_fill_date < today),
                })

            filled = [o for o in openings if o.status == "filled" and o.filled_ts]
            avg_fill_days = (
                sum((o.filled_ts.date() - o.opened_ts.date()).days for o in filled) / len(filled)
                if filled else None
            )

            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "total_openings": len(openings),
                    "open_count": sum(1 for o in openings if o.status == "open"),
                    "filled_count": len(filled),
                    "avg_fill_days": round(avg_fill_days, 1) if avg_fill_days else None,
                    "target_fill_days": 30,
                    "pipeline": pipeline,
                },
                actions_taken=["pipeline_report_generated"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")

    @staticmethod
    def _draft_jd(payload: dict) -> dict:
        title = payload.get("title", "")
        dept = payload.get("department", "")
        return {
            "title": title,
            "department": dept,
            "summary": (
                f"We are looking for a {title} to join the {dept} team at "
                f"Yexis Electronics. The role involves {payload.get('notes', 'responsibilities as defined by the manager')}."
            ),
            "requirements": [
                "Relevant educational background",
                "Prior experience in a similar role",
                "Strong communication skills",
            ],
            "status": "draft — pending HR Manager review before publication",
        }
