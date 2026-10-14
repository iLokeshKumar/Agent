"""M1 ISM Command — Internal Sales Manager command centre.

Autonomy: A1 (read-only command centre — surfaces intelligence for ISM action;
never writes to deals, invoices, or CRM autonomously).

Actions:
  pipeline   — dealer pipeline overview with outstanding and offtake
  targets    — target-vs-actual for current period
  visits     — generate dealer visit plan based on priority score
  alerts     — urgent items: overdue payments, expiring schemes, stalled deals
"""

import uuid
from datetime import date, datetime, timedelta, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"pipeline", "targets", "visits", "alerts"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}

VISIT_TOP_N = 10  # top-N dealers to visit per week


class M1IsmCommand(BaseAgent):
    """ISM Command — M1

    Internal Sales Manager command centre:
    - Pipeline snapshot from DealerMart
    - Outstanding collections surfaced for F1 handoff
    - Prioritised dealer visit list
    - Urgent alerts: overdue, expiring promos, stalled offtake
    - A1 only: all outputs are advisory; ISM takes action via other agents
    """

    agent_code = "M1"
    agent_name = "ISM Command"

    def describe(self) -> str:
        return "Internal Sales Manager command centre — pipeline, targets, visits, and alerts."

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
        from app.models.enterprise import DealerMart, KpiSnapshot

        action = payload["action"]
        entity = payload.get("entity", "YEXIS-EL")
        branch = payload.get("branch")
        today = date.today()

        q = db.query(DealerMart)
        if branch:
            q = q.filter(DealerMart.branch == branch)
        dealers = q.all()

        if action == "pipeline":
            total_outstanding = sum(d.outstanding_amt or 0 for d in dealers)
            overdue_dealers = [
                {
                    "dealer_ref": d.dealer_ref,
                    "branch": d.branch,
                    "outstanding_amt": d.outstanding_amt,
                    "overdue_buckets": d.overdue_buckets_json,
                    "payment_score": d.payment_behavior_score,
                }
                for d in dealers
                if d.overdue_buckets_json and any(
                    v > 0 for k, v in d.overdue_buckets_json.items()
                    if k in ("d31_60", "d61_90", "over_90")
                )
            ]
            overdue_dealers.sort(key=lambda x: x["outstanding_amt"] or 0, reverse=True)

            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "branch": branch,
                    "total_dealers": len(dealers),
                    "total_outstanding": round(total_outstanding, 2),
                    "overdue_dealers_count": len(overdue_dealers),
                    "top_overdue": overdue_dealers[:10],
                    "as_of": str(today),
                },
                actions_taken=["ism_pipeline_generated"],
            )

        if action == "targets":
            kpi_row = (
                db.query(KpiSnapshot)
                .filter(
                    KpiSnapshot.kpi_id == "total_open_receivables",
                    KpiSnapshot.entity == entity,
                )
                .order_by(KpiSnapshot.period.desc())
                .first()
            )
            dso_row = (
                db.query(KpiSnapshot)
                .filter(KpiSnapshot.kpi_id == "dso_days", KpiSnapshot.entity == entity)
                .order_by(KpiSnapshot.period.desc())
                .first()
            )
            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "total_open_receivables": kpi_row.value if kpi_row else None,
                    "dso_days": dso_row.value if dso_row else None,
                    "dso_target": 42,
                    "dso_status": (
                        "ON_TARGET" if (dso_row and dso_row.value and dso_row.value <= 42)
                        else "ABOVE_TARGET"
                    ),
                    "period": str(kpi_row.period) if kpi_row else str(today),
                },
                actions_taken=["ism_targets_report"],
            )

        if action == "visits":
            # Priority = high outstanding + low payment score
            def _priority(d: DealerMart) -> float:
                outstanding = d.outstanding_amt or 0
                score = d.payment_behavior_score or 50
                # Normalise: high outstanding + low score = high priority
                return outstanding * (100 - score) / 100

            sorted_dealers = sorted(dealers, key=_priority, reverse=True)[:VISIT_TOP_N]
            visit_plan = [
                {
                    "rank": i + 1,
                    "dealer_ref": d.dealer_ref,
                    "branch": d.branch,
                    "outstanding_amt": d.outstanding_amt,
                    "payment_score": d.payment_behavior_score,
                    "priority_score": round(_priority(d), 0),
                    "suggested_action": (
                        "Collect overdue payment" if (d.outstanding_amt or 0) > 100000 else
                        "Scheme push + relationship"
                    ),
                }
                for i, d in enumerate(sorted_dealers)
            ]
            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "branch": branch,
                    "week_of": str(today - timedelta(days=today.weekday())),
                    "visit_plan": visit_plan,
                    "autonomy": "A1 — visit plan is advisory; ISM schedules and records visits",
                },
                actions_taken=["visit_plan_generated"],
            )

        if action == "alerts":
            # Overdue > 60 days
            high_risk = [
                d for d in dealers
                if d.overdue_buckets_json and (
                    (d.overdue_buckets_json.get("d61_90") or 0) +
                    (d.overdue_buckets_json.get("over_90") or 0) > 0
                )
            ]
            # Stalled: no order in 30+ days
            stalled = [
                d for d in dealers
                if d.last_order_ts and (datetime.now(timezone.utc) - d.last_order_ts).days > 30
            ]
            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "high_overdue_dealers": [
                        {"dealer_ref": d.dealer_ref, "branch": d.branch,
                         "d61_90": (d.overdue_buckets_json or {}).get("d61_90", 0),
                         "over_90": (d.overdue_buckets_json or {}).get("over_90", 0)}
                        for d in high_risk[:20]
                    ],
                    "stalled_dealers": [
                        {"dealer_ref": d.dealer_ref, "branch": d.branch,
                         "last_order": d.last_order_ts.strftime("%Y-%m-%d") if d.last_order_ts else None}
                        for d in stalled[:20]
                    ],
                    "alert_counts": {
                        "high_overdue": len(high_risk),
                        "stalled_no_order_30d": len(stalled),
                    },
                },
                actions_taken=["ism_alerts_generated"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
