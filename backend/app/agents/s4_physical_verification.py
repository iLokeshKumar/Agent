"""S4 Physical Verification — stock count vs serial registry reconciliation.

Autonomy: A2 (supervised — count batch is automatic; discrepancy resolution requires
Store Manager approval).

Actions:
  start      — open a new count batch for a branch
  record     — add scanned serials to the open batch
  reconcile  — diff physical count against SerialRegistry
  approve    — mark batch reconciled (Store Manager action)
  report     — summary of a batch or all open batches
"""

import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"start", "record", "reconcile", "approve", "report"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}


class S4PhysicalVerification(BaseAgent):
    """Physical Verification Agent — S4

    Manages the periodic physical stock count process:
    - Opens count batches per branch
    - Accumulates scanned serials
    - Reconciles against SerialRegistry to surface missing/extra/mismatch
    - Store Manager approves the reconciled report
    - A2: count and diff are automatic; resolution of discrepancies needs human sign-off
    """

    agent_code = "S4"
    agent_name = "Physical Verification"

    def describe(self) -> str:
        return "Physical stock count vs serial registry reconciliation."

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
            if not payload.get("batch_id"):
                return "'batch_id' is required for record"
            serials = payload.get("serials", [])
            if not serials:
                return "'serials' list is required and must not be empty"
            if not isinstance(serials, list):
                return "'serials' must be a list of strings"
        if action in {"reconcile", "approve"}:
            if not payload.get("batch_id"):
                return f"'batch_id' is required for {action}"
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
        from app.models.enterprise import PhysicalCountBatch, PhysicalCountLine, SerialRegistry

        action = payload["action"]
        entity = payload.get("entity", "YEXIS-EL")
        now = datetime.now(timezone.utc)

        if action == "start":
            batch = PhysicalCountBatch(
                entity=entity,
                branch=payload.get("branch"),
                counted_by=requested_by or payload.get("counted_by"),
                status="open",
            )
            db.add(batch)
            db.commit()
            db.refresh(batch)
            return AgentResult(
                status="SUCCESS",
                output={
                    "batch_id": str(batch.id),
                    "entity": entity,
                    "branch": batch.branch,
                    "status": batch.status,
                    "next_step": "Scan all serials in the branch and submit via action=record.",
                },
                actions_taken=["count_batch_opened"],
            )

        if action == "record":
            batch = db.get(PhysicalCountBatch, payload["batch_id"])
            if batch is None:
                return AgentResult(status="VALIDATION_ERROR", error=f"Batch '{payload['batch_id']}' not found")
            if batch.status != "open":
                return AgentResult(status="VALIDATION_ERROR", error=f"Batch is {batch.status} — can only record into open batches")

            serials = payload["serials"]
            added = 0
            skipped = 0
            for serial in serials:
                if not isinstance(serial, str) or not serial.strip():
                    skipped += 1
                    continue
                existing = db.query(PhysicalCountLine).filter(
                    PhysicalCountLine.batch_id == batch.id,
                    PhysicalCountLine.serial == serial.strip(),
                ).first()
                if existing:
                    skipped += 1
                    continue
                line = PhysicalCountLine(
                    batch_id=batch.id,
                    serial=serial.strip(),
                    sku_ref=payload.get("sku_ref"),
                )
                db.add(line)
                added += 1

            batch.total_counted = (batch.total_counted or 0) + added
            db.commit()
            return AgentResult(
                status="SUCCESS",
                output={
                    "batch_id": str(batch.id),
                    "serials_added": added,
                    "serials_skipped_duplicate": skipped,
                    "total_counted_so_far": batch.total_counted,
                },
                actions_taken=[f"recorded_{added}_serials"],
            )

        if action == "reconcile":
            batch = db.get(PhysicalCountBatch, payload["batch_id"])
            if batch is None:
                return AgentResult(status="VALIDATION_ERROR", error=f"Batch '{payload['batch_id']}' not found")

            lines = db.query(PhysicalCountLine).filter(PhysicalCountLine.batch_id == batch.id).all()
            counted_serials = {ln.serial for ln in lines}

            # Registry: all in_stock/reserved serials for this entity+branch
            q = db.query(SerialRegistry).filter(SerialRegistry.entity == entity)
            if batch.branch:
                q = q.filter(SerialRegistry.location_ref == batch.branch)
            registry_rows = q.all()
            registry_serials = {r.serial: r for r in registry_rows}
            expected_serials = {s for s, r in registry_serials.items() if r.status in ("in_stock", "reserved")}

            matched = counted_serials & expected_serials
            extra = counted_serials - expected_serials
            missing = expected_serials - counted_serials
            status_mismatch = []
            for serial in counted_serials:
                reg = registry_serials.get(serial)
                if reg and reg.status not in ("in_stock", "reserved"):
                    status_mismatch.append({"serial": serial, "registry_status": reg.status})

            # Update line results
            for ln in lines:
                if ln.serial in matched:
                    ln.result = "matched"
                elif ln.serial in extra:
                    ln.result = "extra"
                else:
                    ln.result = "extra"
            for serial in missing:
                ln = PhysicalCountLine(
                    batch_id=batch.id, serial=serial, result="missing"
                )
                db.add(ln)

            batch.matched = len(matched)
            batch.missing_count = len(missing)
            batch.extra_count = len(extra)
            batch.mismatch_count = len(status_mismatch)
            batch.status = "reconciled"
            batch.closed_ts = now
            db.commit()

            discrepancies = len(missing) + len(extra) + len(status_mismatch)
            return AgentResult(
                status="SUCCESS" if discrepancies == 0 else "NEEDS_APPROVAL",
                output={
                    "batch_id": str(batch.id),
                    "entity": entity,
                    "branch": batch.branch,
                    "counted": len(counted_serials),
                    "expected": len(expected_serials),
                    "matched": len(matched),
                    "missing": sorted(missing)[:50],
                    "missing_count": len(missing),
                    "extra": sorted(extra)[:50],
                    "extra_count": len(extra),
                    "status_mismatch": status_mismatch[:50],
                    "mismatch_count": len(status_mismatch),
                    "next_step": (
                        "Store Manager to approve clean count via action=approve."
                        if discrepancies == 0 else
                        f"{discrepancies} discrepancy(ies) require Store Manager review before approval."
                    ),
                },
                actions_taken=["reconciliation_complete"],
            )

        if action == "approve":
            batch = db.get(PhysicalCountBatch, payload["batch_id"])
            if batch is None:
                return AgentResult(status="VALIDATION_ERROR", error=f"Batch '{payload['batch_id']}' not found")
            if batch.status not in ("reconciled", "open"):
                return AgentResult(status="VALIDATION_ERROR", error=f"Batch status is '{batch.status}' — must be reconciled first")
            batch.status = "approved"
            db.commit()
            return AgentResult(
                status="SUCCESS",
                output={"batch_id": str(batch.id), "status": "approved"},
                actions_taken=["count_batch_approved"],
            )

        if action == "report":
            batch_id = payload.get("batch_id")
            if batch_id:
                batch = db.get(PhysicalCountBatch, batch_id)
                if batch is None:
                    return AgentResult(status="VALIDATION_ERROR", error=f"Batch '{batch_id}' not found")
                return AgentResult(
                    status="SUCCESS",
                    output={
                        "batch_id": str(batch.id), "entity": batch.entity,
                        "branch": batch.branch, "status": batch.status,
                        "total_counted": batch.total_counted,
                        "matched": batch.matched, "missing": batch.missing_count,
                        "extra": batch.extra_count, "mismatch": batch.mismatch_count,
                        "started_ts": batch.started_ts.isoformat() if batch.started_ts else None,
                        "closed_ts": batch.closed_ts.isoformat() if batch.closed_ts else None,
                    },
                    actions_taken=["batch_report_generated"],
                )
            batches = db.query(PhysicalCountBatch).filter(
                PhysicalCountBatch.entity == entity,
                PhysicalCountBatch.status.in_(["open", "reconciled"]),
            ).all()
            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "open_batches": [
                        {"batch_id": str(b.id), "branch": b.branch, "status": b.status,
                         "total_counted": b.total_counted, "started_ts": b.started_ts.isoformat()}
                        for b in batches
                    ],
                },
                actions_taken=["open_batches_report"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
