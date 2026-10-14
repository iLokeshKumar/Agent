"""H4 Payroll Check — pre-payment payroll validation.

Autonomy: A1 (propose only). Flags anomalies; never modifies payroll data.

Validates an uploaded payroll_lines array against the live HrEmployee registry:
  1. Headcount check: payroll line count vs active employee count
  2. Employee existence: every payroll employee_id must exist and be active
  3. Ghost employees: employees in payroll not in registry
  4. Missing employees: active employees absent from payroll
  5. Duplicate lines: same employee_id appears more than once
  6. Reasonable salary band: gross > 0 and < max_gross threshold (config)

Payload:
  entity          "YEXIS-EL" | "YEXIS-SL"
  payroll_lines   list of {employee_id, gross_pay, department}
  max_gross       optional per-line ceiling (default 500000 INR)
"""

import uuid
from decimal import Decimal

from app.agents.base import BaseAgent, AgentResult

VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}
DEFAULT_MAX_GROSS = Decimal("500000")


class H4PayrollCheck(BaseAgent):
    """Payroll Check Agent — H4

    Pre-payment validation gate:
    - Compares payroll submission against HrEmployee registry
    - Flags headcount mismatches, ghost/missing employees, duplicates
    - Flags out-of-band gross amounts
    - A1: reports only, never writes or approves payroll data
    """

    agent_code = "H4"
    agent_name = "Payroll Check"

    def describe(self) -> str:
        return "Pre-payment payroll validation against the live employee registry."

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"
        entity = payload.get("entity")
        if not entity:
            return "'entity' is required"
        if entity not in VALID_ENTITIES:
            return f"entity must be one of {sorted(VALID_ENTITIES)}"
        lines = payload.get("payroll_lines")
        if lines is None:
            return "'payroll_lines' is required"
        if not isinstance(lines, list):
            return "'payroll_lines' must be a list"
        if len(lines) == 0:
            return "'payroll_lines' is empty"
        for i, line in enumerate(lines):
            if not isinstance(line, dict):
                return f"payroll_lines[{i}] must be an object"
            if not line.get("employee_id"):
                return f"payroll_lines[{i}] missing 'employee_id'"
            if line.get("gross_pay") is None:
                return f"payroll_lines[{i}] missing 'gross_pay'"
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

        entity = payload["entity"]
        lines = payload["payroll_lines"]
        max_gross = Decimal(str(payload.get("max_gross", DEFAULT_MAX_GROSS)))

        # Load registry
        active_employees = (
            db.query(HrEmployee)
            .filter(HrEmployee.entity == entity, HrEmployee.status == "active")
            .all()
        )
        registry_ids = {emp.employee_id: emp for emp in active_employees}

        anomalies = []

        # 1. Duplicate employee_ids in payroll
        seen_ids: dict[str, int] = {}
        for i, line in enumerate(lines):
            eid = line["employee_id"]
            seen_ids[eid] = seen_ids.get(eid, 0) + 1
        for eid, count in seen_ids.items():
            if count > 1:
                anomalies.append({
                    "type": "duplicate_employee",
                    "severity": "HIGH",
                    "employee_id": eid,
                    "detail": f"Employee '{eid}' appears {count} times in payroll",
                })

        # 2. Ghost employees (in payroll, not in registry)
        payroll_ids = {line["employee_id"] for line in lines}
        ghost_ids = payroll_ids - set(registry_ids.keys())
        for eid in sorted(ghost_ids):
            anomalies.append({
                "type": "ghost_employee",
                "severity": "HIGH",
                "employee_id": eid,
                "detail": f"Employee '{eid}' in payroll not found in active registry",
            })

        # 3. Missing employees (active, not in payroll)
        missing_ids = set(registry_ids.keys()) - payroll_ids
        for eid in sorted(missing_ids):
            emp = registry_ids[eid]
            anomalies.append({
                "type": "missing_employee",
                "severity": "MEDIUM",
                "employee_id": eid,
                "detail": f"Active employee '{eid}' ({emp.name}) absent from payroll",
            })

        # 4. Gross pay range checks
        for line in lines:
            eid = line["employee_id"]
            try:
                gross = Decimal(str(line["gross_pay"]))
            except Exception:
                anomalies.append({
                    "type": "invalid_gross",
                    "severity": "HIGH",
                    "employee_id": eid,
                    "detail": f"gross_pay '{line['gross_pay']}' is not a valid number",
                })
                continue
            if gross <= 0:
                anomalies.append({
                    "type": "zero_or_negative_gross",
                    "severity": "HIGH",
                    "employee_id": eid,
                    "detail": f"gross_pay {gross} is zero or negative",
                })
            elif gross > max_gross:
                anomalies.append({
                    "type": "gross_exceeds_ceiling",
                    "severity": "MEDIUM",
                    "employee_id": eid,
                    "detail": f"gross_pay ₹{gross:,.0f} exceeds ceiling ₹{max_gross:,.0f}",
                })

        # 5. Headcount summary
        registry_count = len(active_employees)
        payroll_count = len(lines)
        hc_match = registry_count == payroll_count

        high_anomalies = [a for a in anomalies if a["severity"] == "HIGH"]
        medium_anomalies = [a for a in anomalies if a["severity"] == "MEDIUM"]

        status = "SUCCESS" if not high_anomalies else "NEEDS_APPROVAL"

        return AgentResult(
            status=status,
            output={
                "entity": entity,
                "payroll_line_count": payroll_count,
                "registry_active_count": registry_count,
                "headcount_match": hc_match,
                "total_anomalies": len(anomalies),
                "high_severity": len(high_anomalies),
                "medium_severity": len(medium_anomalies),
                "anomalies": anomalies,
                "recommendation": (
                    "DO NOT PROCESS: High-severity anomalies require resolution before payroll run."
                    if high_anomalies else
                    "LOW RISK: Review medium anomalies, then proceed with payroll processing." if medium_anomalies else
                    "CLEAR: No anomalies detected. Payroll is consistent with registry."
                ),
                "autonomy": "A1 — validation report only, payroll processing requires human approval",
            },
            actions_taken=[
                f"validated_{payroll_count}_payroll_lines",
                f"checked_against_{registry_count}_active_employees",
                f"found_{len(anomalies)}_anomalies",
            ],
        )
