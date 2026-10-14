"""C3 SCM Claims Tracker — supply-chain claims against Samsung and logistics partners.

Autonomy: A2 (supervised — claim intake automatic; settlement acceptance
requires Finance/SCM Manager approval).

Claim types:
  short_supply  — Samsung delivered fewer units than invoiced
  damage        — damaged units received in shipment
  price_diff    — invoice price differs from PO price
  scheme        — Samsung scheme credit not received
  logistics     — logistics partner damage or loss

Actions:
  intake   — record a new SCM claim
  update   — update status or add settlement amount
  report   — claim pipeline summary
"""

import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"intake", "update", "report"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}
VALID_CLAIM_TYPES = {"short_supply", "damage", "price_diff", "scheme", "logistics"}
VALID_STATUSES = {"open", "submitted", "acknowledged", "settled", "rejected"}


class C3ClaimsTracker(BaseAgent):
    """SCM Claims Tracker — C3

    Manages supply-chain claims lifecycle:
    - Records claims against Samsung and logistics partners
    - Tracks from open → submitted → acknowledged → settled
    - Surfaces aging claims for Finance review
    - A2: claim intake and status updates are automatic;
      settlement acceptance requires Finance/SCM Manager sign-off
    """

    agent_code = "C3"
    agent_name = "SCM Claims Tracker"

    def describe(self) -> str:
        return "SCM claims lifecycle — Samsung short-supply, damage, price-diff, scheme, logistics."

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
        if action == "intake":
            if not payload.get("claim_ref"):
                return "'claim_ref' is required for intake"
            ct = payload.get("claim_type")
            if not ct:
                return "'claim_type' is required for intake"
            if ct not in VALID_CLAIM_TYPES:
                return f"claim_type must be one of {sorted(VALID_CLAIM_TYPES)}"
            amt = payload.get("amount_claimed")
            if amt is not None:
                try:
                    v = float(amt)
                except (TypeError, ValueError):
                    return "'amount_claimed' must be a number"
                if v < 0:
                    return "'amount_claimed' cannot be negative"
        if action == "update":
            if not payload.get("claim_ref"):
                return "'claim_ref' is required for update"
            ns = payload.get("status")
            if ns and ns not in VALID_STATUSES:
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
        from app.models.enterprise import ScmClaim

        action = payload["action"]
        entity = payload.get("entity", "YEXIS-EL")
        now = datetime.now(timezone.utc)

        if action == "intake":
            claim_ref = payload["claim_ref"]
            existing = db.query(ScmClaim).filter(ScmClaim.claim_ref == claim_ref, ScmClaim.entity == entity).first()
            if existing:
                return AgentResult(
                    status="SUCCESS",
                    output={"message": f"Claim '{claim_ref}' already recorded.", "status": existing.status},
                    actions_taken=[],
                )
            amt = payload.get("amount_claimed")
            claim = ScmClaim(
                claim_ref=claim_ref,
                claim_type=payload["claim_type"],
                entity=entity,
                counterparty_ref=payload.get("counterparty_ref", "Samsung India"),
                invoice_ref=payload.get("invoice_ref"),
                amount_claimed=float(amt) if amt is not None else None,
                notes=payload.get("notes"),
                evidence_refs_json=payload.get("evidence_refs"),
                status="open",
            )
            db.add(claim)
            db.commit()
            return AgentResult(
                status="SUCCESS",
                output={
                    "claim_ref": claim_ref,
                    "claim_type": claim.claim_type,
                    "entity": entity,
                    "amount_claimed": claim.amount_claimed,
                    "status": "open",
                    "next_step": "SCM team to submit supporting evidence and escalate to counterparty.",
                },
                actions_taken=["scm_claim_recorded"],
            )

        if action == "update":
            claim_ref = payload["claim_ref"]
            claim = db.query(ScmClaim).filter(ScmClaim.claim_ref == claim_ref, ScmClaim.entity == entity).first()
            if claim is None:
                return AgentResult(status="VALIDATION_ERROR", error=f"Claim '{claim_ref}' not found")

            new_status = payload.get("status")
            if new_status:
                if new_status == "submitted" and claim.status != "open":
                    return AgentResult(status="VALIDATION_ERROR", error=f"Can only submit from 'open' status (current: {claim.status})")
                if new_status in ("settled", "rejected") and claim.status not in ("acknowledged", "submitted"):
                    return AgentResult(status="VALIDATION_ERROR", error=f"Cannot settle/reject from '{claim.status}'")
                claim.status = new_status
                if new_status == "submitted":
                    claim.submitted_ts = now
                if new_status == "settled":
                    claim.settled_ts = now
                    if payload.get("amount_settled") is not None:
                        claim.amount_settled = float(payload["amount_settled"])

            if payload.get("notes"):
                claim.notes = (claim.notes or "") + f"\n{now.date()}: {payload['notes']}"
            db.commit()

            return AgentResult(
                status="SUCCESS" if new_status != "settled" else "NEEDS_APPROVAL",
                output={
                    "claim_ref": claim_ref,
                    "status": claim.status,
                    "amount_claimed": claim.amount_claimed,
                    "amount_settled": claim.amount_settled,
                    "settled_ts": claim.settled_ts.isoformat() if claim.settled_ts else None,
                },
                actions_taken=[f"claim_updated:{new_status}" if new_status else "claim_updated"],
            )

        if action == "report":
            claims = db.query(ScmClaim).filter(ScmClaim.entity == entity).all()
            by_status: dict[str, int] = {}
            by_type: dict[str, int] = {}
            total_open_value = 0.0
            for c in claims:
                by_status[c.status] = by_status.get(c.status, 0) + 1
                by_type[c.claim_type] = by_type.get(c.claim_type, 0) + 1
                if c.status in ("open", "submitted", "acknowledged") and c.amount_claimed:
                    total_open_value += c.amount_claimed

            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "total_claims": len(claims),
                    "by_status": by_status,
                    "by_type": by_type,
                    "total_open_claims_value": round(total_open_value, 2),
                },
                actions_taken=["scm_claims_report"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
