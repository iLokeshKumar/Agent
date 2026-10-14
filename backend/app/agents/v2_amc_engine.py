"""V2 AMC Engine — Annual Maintenance Contract lifecycle management.

Autonomy: A2 (supervised — renewal reminders automatic; contract creation
and value changes require Service Manager approval).

Actions:
  check    — scan InstalledBase + AmcContract for expiring/expired contracts
  renew    — create a renewal AmcContract record (A2, proposed)
  report   — AMC portfolio summary by status
  register — create a new AmcContract for an installed asset (Service Manager gate)
"""

import uuid
from datetime import date, datetime, timedelta, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"check", "renew", "report", "register"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}

EXPIRY_ALERT_DAYS = 90   # alert window before expiry
EXPIRED_GRACE_DAYS = 30  # show recently-expired contracts too


class V2AmcEngine(BaseAgent):
    """AMC Engine — V2

    Manages the Annual Maintenance Contract portfolio:
    - Surfaces contracts expiring within 90 days for proactive renewal
    - Proposes renewals for Service Manager approval (A2)
    - Tracks renewal pipeline: alert → proposed → renewed
    - KPI: amc_renewal_rate_pct — renewed / (expiring in 90 days)
    """

    agent_code = "V2"
    agent_name = "AMC Engine"

    def describe(self) -> str:
        return "AMC contract lifecycle — expiry alerts, renewal proposals, and portfolio reporting."

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
        if action == "renew":
            if not payload.get("contract_ref"):
                return "'contract_ref' is required for renew"
        if action == "register":
            for req in ("asset_id", "start_date", "end_date"):
                if not payload.get(req):
                    return f"'{req}' is required for register"
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
        from app.models.enterprise import AmcContract, InstalledBase

        action = payload["action"]
        entity = payload.get("entity", "YEXIS-EL")
        today = date.today()

        if action == "check":
            alert_cutoff = today + timedelta(days=EXPIRY_ALERT_DAYS)
            expired_cutoff = today - timedelta(days=EXPIRED_GRACE_DAYS)

            # Expiring soon
            expiring = db.query(AmcContract).filter(
                AmcContract.entity == entity,
                AmcContract.status == "active",
                AmcContract.end_date <= alert_cutoff,
                AmcContract.end_date >= today,
            ).all()

            # Recently expired + not renewed
            expired = db.query(AmcContract).filter(
                AmcContract.entity == entity,
                AmcContract.status == "active",
                AmcContract.end_date < today,
                AmcContract.end_date >= expired_cutoff,
            ).all()

            def _fmt(c: AmcContract) -> dict:
                days_to_expiry = (c.end_date - today).days
                return {
                    "contract_ref": c.contract_ref,
                    "asset_id": c.asset_id,
                    "customer_ref": c.customer_ref,
                    "end_date": str(c.end_date),
                    "days_to_expiry": days_to_expiry,
                    "value": c.value,
                }

            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "as_of_date": str(today),
                    "expiring_in_90d_count": len(expiring),
                    "expired_unrenewed_count": len(expired),
                    "expiring_in_90d": [_fmt(c) for c in sorted(expiring, key=lambda c: c.end_date)],
                    "expired_unrenewed": [_fmt(c) for c in sorted(expired, key=lambda c: c.end_date)],
                    "proposal": (
                        f"{len(expiring)} contract(s) expiring within 90 days. "
                        f"Run action=renew for each to generate renewal proposals."
                    ) if expiring else "No contracts expiring within 90 days.",
                    "autonomy": "A2 — alerts only, renewal proposals require Service Manager approval",
                },
                actions_taken=["amc_expiry_check"],
            )

        if action == "renew":
            contract_ref = payload["contract_ref"]
            orig = db.query(AmcContract).filter(AmcContract.contract_ref == contract_ref).first()
            if orig is None:
                return AgentResult(status="VALIDATION_ERROR", error=f"Contract '{contract_ref}' not found")

            renewal_ref = f"{contract_ref}-R1"
            existing_renewal = db.query(AmcContract).filter(AmcContract.contract_ref == renewal_ref).first()
            if existing_renewal:
                return AgentResult(
                    status="SUCCESS",
                    output={"message": f"Renewal '{renewal_ref}' already exists.", "renewal_ref": renewal_ref},
                    actions_taken=[],
                )

            # Proposed renewal: starts day after original end
            new_start = orig.end_date + timedelta(days=1)
            new_end = date(new_start.year + 1, new_start.month, new_start.day) - timedelta(days=1)

            renewal = AmcContract(
                contract_ref=renewal_ref,
                asset_id=orig.asset_id,
                customer_ref=orig.customer_ref,
                entity=orig.entity,
                start_date=new_start,
                end_date=new_end,
                value=orig.value,
                status="active",
            )
            db.add(renewal)
            orig.renewal_ref = renewal_ref
            orig.status = "renewed"
            orig.renewed_ts = datetime.now(timezone.utc)
            db.commit()

            return AgentResult(
                status="NEEDS_APPROVAL",
                output={
                    "original_contract": contract_ref,
                    "renewal_ref": renewal_ref,
                    "asset_id": orig.asset_id,
                    "customer_ref": orig.customer_ref,
                    "new_start": str(new_start),
                    "new_end": str(new_end),
                    "value": orig.value,
                    "next_step": "Service Manager to review and confirm renewal with customer.",
                },
                actions_taken=["renewal_proposed"],
            )

        if action == "register":
            asset_id = payload["asset_id"]
            try:
                start_date = date.fromisoformat(payload["start_date"])
                end_date = date.fromisoformat(payload["end_date"])
            except ValueError as e:
                return AgentResult(status="VALIDATION_ERROR", error=f"Invalid date: {e}")
            if end_date <= start_date:
                return AgentResult(status="VALIDATION_ERROR", error="end_date must be after start_date")

            contract_ref = payload.get("contract_ref") or f"AMC-{asset_id}-{start_date.strftime('%Y%m')}"
            contract = AmcContract(
                contract_ref=contract_ref,
                asset_id=asset_id,
                customer_ref=payload.get("customer_ref"),
                entity=entity,
                start_date=start_date,
                end_date=end_date,
                value=payload.get("value"),
                status="active",
            )
            db.add(contract)
            db.commit()
            return AgentResult(
                status="SUCCESS",
                output={
                    "contract_ref": contract_ref,
                    "asset_id": asset_id,
                    "entity": entity,
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                },
                actions_taken=["amc_contract_registered"],
            )

        if action == "report":
            contracts = db.query(AmcContract).filter(AmcContract.entity == entity).all()
            by_status: dict[str, int] = {}
            total_value = 0.0
            for c in contracts:
                by_status[c.status] = by_status.get(c.status, 0) + 1
                if c.status == "active" and c.value:
                    total_value += c.value

            expiring_soon = sum(
                1 for c in contracts
                if c.status == "active" and c.end_date and today <= c.end_date <= today + timedelta(days=EXPIRY_ALERT_DAYS)
            )
            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "total_contracts": len(contracts),
                    "by_status": by_status,
                    "active_portfolio_value": round(total_value, 2),
                    "expiring_in_90d": expiring_soon,
                },
                actions_taken=["amc_portfolio_report"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
