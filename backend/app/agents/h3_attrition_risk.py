"""H3 Attrition Risk — employee engagement & flight-risk scoring.

Autonomy: A1 (propose only). Scores HrEmployee rows; never takes
autonomous action on an employee record.

Score model (0–100, higher = higher risk):
  - tenure_score:       <6 months → 30pts, 6–12m → 20pts, 1–2y → 10pts, >2y → 0pts
  - engagement_score:   last_engagement_ts >90d → 30pts, >60d → 20pts, >30d → 10pts, recent → 0pts
  - role_mismatch:      reserved → 0pts (future: Zoho HRMS signal)
  Total capped at 100.

KPI: attrition_risk_score (org-level mean risk of high-risk pool)

Actions:
  score    — (re)score all active employees for an entity
  report   — return scored list filtered by risk tier
"""

import uuid
from datetime import date, datetime, timezone, timedelta

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"score", "report"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}

RISK_HIGH = 60
RISK_MEDIUM = 30


def _tenure_pts(join_date: date | None, today: date) -> int:
    if join_date is None:
        return 10
    months = (today.year - join_date.year) * 12 + (today.month - join_date.month)
    if months < 6:
        return 30
    if months < 12:
        return 20
    if months < 24:
        return 10
    return 0


def _engagement_pts(last_engagement_ts: datetime | None, today: date) -> int:
    if last_engagement_ts is None:
        return 25
    days_since = (datetime.now(timezone.utc) - last_engagement_ts).days
    if days_since > 90:
        return 30
    if days_since > 60:
        return 20
    if days_since > 30:
        return 10
    return 0


class H3AttritionRisk(BaseAgent):
    """Attrition Risk Agent — H3

    Scores active employees based on:
    - Tenure (new joiners and 6–12 month window are higher risk)
    - Engagement staleness (last_engagement_ts gap)
    - Writes risk_score back to HrEmployee
    - Proposes high-risk employees for manager review — A1 only
    """

    agent_code = "H3"
    agent_name = "Attrition Risk"

    def describe(self) -> str:
        return "Employee flight-risk scoring based on tenure and engagement signals."

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"
        action = payload.get("action")
        if not action:
            return "'action' is required"
        if action not in VALID_ACTIONS:
            return f"action must be one of {sorted(VALID_ACTIONS)}"
        entity = payload.get("entity", "YEXIS-EL")
        if entity not in VALID_ENTITIES:
            return f"entity must be one of {sorted(VALID_ENTITIES)}"
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
        from app.models.enterprise import HrEmployee

        action = payload.get("action", "score")
        entity = payload.get("entity", "YEXIS-EL")
        today = date.today()

        employees = (
            db.query(HrEmployee)
            .filter(HrEmployee.entity == entity, HrEmployee.status == "active")
            .all()
        )

        if not employees:
            return AgentResult(
                status="SUCCESS",
                output={"entity": entity, "active_count": 0, "scored": 0, "high_risk": []},
                actions_taken=["score_run_empty"],
            )

        scored = []
        for emp in employees:
            tenure_pts = _tenure_pts(emp.join_date, today)
            engagement_pts = _engagement_pts(emp.last_engagement_ts, today)
            score = min(100, tenure_pts + engagement_pts)

            if action == "score":
                emp.risk_score = score

            tier = "high" if score >= RISK_HIGH else ("medium" if score >= RISK_MEDIUM else "low")
            scored.append({
                "employee_id": emp.employee_id,
                "name": emp.name,
                "department": emp.department,
                "tenure_pts": tenure_pts,
                "engagement_pts": engagement_pts,
                "risk_score": score,
                "risk_tier": tier,
            })

        if action == "score":
            db.commit()

        scored.sort(key=lambda x: x["risk_score"], reverse=True)

        high_risk = [e for e in scored if e["risk_tier"] == "high"]
        medium_risk = [e for e in scored if e["risk_tier"] == "medium"]
        mean_score = sum(e["risk_score"] for e in scored) / len(scored) if scored else 0

        min_tier = payload.get("min_tier", "high") if action == "report" else "high"
        visible = [e for e in scored if e["risk_tier"] in {"high", "medium", "low"}]
        if min_tier == "high":
            visible = high_risk
        elif min_tier == "medium":
            visible = high_risk + medium_risk

        actions_taken = []
        if action == "score":
            actions_taken = [
                f"scored_{len(employees)}_employees",
                f"org_mean_risk={round(mean_score, 1)}",
            ]

        return AgentResult(
            status="SUCCESS",
            output={
                "entity": entity,
                "scored_date": str(today),
                "active_employees": len(employees),
                "org_mean_risk_score": round(mean_score, 1),
                "high_risk_count": len(high_risk),
                "medium_risk_count": len(medium_risk),
                "low_risk_count": len(scored) - len(high_risk) - len(medium_risk),
                "employees": visible,
                "proposal": (
                    f"{len(high_risk)} employee(s) flagged as high-risk. "
                    "Recommend 1:1 check-in by their managers within 7 days."
                ) if high_risk else "No high-risk employees detected.",
                "autonomy": "A1 — proposals only, no automatic actions taken",
            },
            actions_taken=actions_taken if actions_taken else ["risk_report_generated"],
        )
