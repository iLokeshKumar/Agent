"""C2 Dispatch Tracker — inbound/outbound dispatch and POD management.

Autonomy: A2 (supervised — dispatch creation automatic; POD confirmation
and exception handling require Logistics/Store Manager).

Actions:
  record    — create a new dispatch record
  update    — add POD / update status
  report    — dispatch pipeline summary
  overdue   — surface dispatches past expected delivery date
"""

import uuid
from datetime import date, datetime, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"record", "update", "report", "overdue"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}
VALID_DISPATCH_TYPES = {"inbound", "outbound"}
VALID_STATUSES = {"dispatched", "in_transit", "delivered", "delayed", "exception"}


class C2DispatchTracker(BaseAgent):
    """Dispatch Tracker — C2

    Tracks all dispatches in the supply chain:
    - Inbound: Samsung → Yexis branch
    - Outbound: Yexis branch → Dealer
    - POD confirmation updates status to delivered
    - Surfaces overdue dispatches for escalation
    """

    agent_code = "C2"
    agent_name = "Dispatch Tracker"

    def describe(self) -> str:
        return "Inbound/outbound dispatch recording, POD tracking, and overdue alerting."

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
        if action == "record":
            if not payload.get("dispatch_ref"):
                return "'dispatch_ref' is required for record"
            dt = payload.get("dispatch_type")
            if not dt:
                return "'dispatch_type' is required for record (inbound|outbound)"
            if dt not in VALID_DISPATCH_TYPES:
                return f"dispatch_type must be one of {sorted(VALID_DISPATCH_TYPES)}"
        if action == "update":
            if not payload.get("dispatch_ref"):
                return "'dispatch_ref' is required for update"
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
            return self._run(db, payload)
        finally:
            db.close()

    def _run(self, db, payload: dict) -> AgentResult:
        from app.models.enterprise import DispatchRecord

        action = payload["action"]
        entity = payload.get("entity", "YEXIS-EL")
        now = datetime.now(timezone.utc)
        today = date.today()

        if action == "record":
            dispatch_ref = payload["dispatch_ref"]
            existing = db.query(DispatchRecord).filter(
                DispatchRecord.dispatch_ref == dispatch_ref,
                DispatchRecord.entity == entity,
            ).first()
            if existing:
                return AgentResult(
                    status="SUCCESS",
                    output={"message": f"Dispatch '{dispatch_ref}' already recorded.", "status": existing.status},
                    actions_taken=[],
                )
            expected_str = payload.get("expected_delivery")
            expected = None
            if expected_str:
                try:
                    expected = date.fromisoformat(expected_str)
                except ValueError:
                    return AgentResult(status="VALIDATION_ERROR", error=f"expected_delivery '{expected_str}' is not a valid date")

            dispatch = DispatchRecord(
                dispatch_ref=dispatch_ref,
                dispatch_type=payload["dispatch_type"],
                from_ref=payload.get("from_ref"),
                to_ref=payload.get("to_ref"),
                entity=entity,
                invoice_ref=payload.get("invoice_ref"),
                qty=payload.get("qty"),
                dispatch_ts=now,
                expected_delivery=expected,
                status="dispatched",
            )
            db.add(dispatch)
            db.commit()
            return AgentResult(
                status="SUCCESS",
                output={
                    "dispatch_ref": dispatch_ref,
                    "dispatch_type": dispatch.dispatch_type,
                    "from_ref": dispatch.from_ref,
                    "to_ref": dispatch.to_ref,
                    "expected_delivery": str(expected) if expected else None,
                    "status": "dispatched",
                },
                actions_taken=["dispatch_recorded"],
            )

        if action == "update":
            dispatch_ref = payload["dispatch_ref"]
            dispatch = db.query(DispatchRecord).filter(
                DispatchRecord.dispatch_ref == dispatch_ref,
                DispatchRecord.entity == entity,
            ).first()
            if dispatch is None:
                return AgentResult(status="VALIDATION_ERROR", error=f"Dispatch '{dispatch_ref}' not found")

            new_status = payload.get("status")
            if new_status:
                dispatch.status = new_status
                if new_status == "delivered":
                    dispatch.pod_ts = now
                    dispatch.pod_ref = payload.get("pod_ref")
            if payload.get("delay_reason"):
                dispatch.delay_reason = payload["delay_reason"]
            db.commit()
            return AgentResult(
                status="SUCCESS",
                output={
                    "dispatch_ref": dispatch_ref,
                    "status": dispatch.status,
                    "pod_ts": dispatch.pod_ts.isoformat() if dispatch.pod_ts else None,
                    "pod_ref": dispatch.pod_ref,
                },
                actions_taken=[f"dispatch_updated:{new_status}" if new_status else "dispatch_updated"],
            )

        if action == "overdue":
            overdue = db.query(DispatchRecord).filter(
                DispatchRecord.entity == entity,
                DispatchRecord.status.in_(["dispatched", "in_transit"]),
                DispatchRecord.expected_delivery < today,
            ).order_by(DispatchRecord.expected_delivery).all()
            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "overdue_count": len(overdue),
                    "overdue_dispatches": [
                        {
                            "dispatch_ref": d.dispatch_ref,
                            "dispatch_type": d.dispatch_type,
                            "from_ref": d.from_ref,
                            "to_ref": d.to_ref,
                            "expected_delivery": str(d.expected_delivery),
                            "days_overdue": (today - d.expected_delivery).days,
                            "invoice_ref": d.invoice_ref,
                        }
                        for d in overdue
                    ],
                },
                actions_taken=["overdue_check_complete"],
            )

        if action == "report":
            dt_filter = payload.get("dispatch_type")
            q = db.query(DispatchRecord).filter(DispatchRecord.entity == entity)
            if dt_filter:
                q = q.filter(DispatchRecord.dispatch_type == dt_filter)
            records = q.order_by(DispatchRecord.created_ts.desc()).limit(100).all()
            by_status: dict[str, int] = {}
            for r in records:
                by_status[r.status] = by_status.get(r.status, 0) + 1
            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "total_records": len(records),
                    "by_status": by_status,
                    "recent": [
                        {
                            "dispatch_ref": r.dispatch_ref,
                            "type": r.dispatch_type,
                            "status": r.status,
                            "from_ref": r.from_ref,
                            "to_ref": r.to_ref,
                            "expected_delivery": str(r.expected_delivery) if r.expected_delivery else None,
                        }
                        for r in records[:20]
                    ],
                },
                actions_taken=["dispatch_report_generated"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
