"""F2 — Scheme Claims (Yexis-unique finance agent).

Per the build matrix: "Earned-claim computation job over Books+serials
(code); circular extraction class (WP-4); claim-pack template". This
agent is the computation + claim-lifecycle half. The deterministic
earned-claim engine lives in app/services/scheme_claims.py — shared
with any future claim mart so agent output and mart rows can never
disagree. Money is never prompt-math.

Lifecycle per the Tech Spec §7.2:
  computed → packed → submitted → partial|settled|disputed

Payload:
  {
    "action": "compute" | "record_claim" | "record_settlement",
    "scheme_id": str,
    "dealer_ref"?: str, "entity"?: str ("YEXIS-EL" default),
    "serials"?: [{serial, sku_ref, status, sold_invoice_ref,
                  sale_date, base_value}],      # compute
    "already_claimed"?: float, "slabs"?: [...], # compute (optional)
    "amount_claimed"?: float,                   # record_claim
    "amount_received"?: float, "variance_reason"?: str  # settlement
  }

Autonomy: computing is A2 (facts only, nothing leaves the building).
Recording a claim (money leaving as a credit note expectation) and
recording a settlement are A1 — explicit human action, recorded in the
ledger by the caller.
"""

import logging
import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent, AgentResult
from app.services import scheme_claims
from app.services.scheme_claims import SchemeWindowError

logger = logging.getLogger("agent.F2")

VALID_ACTIONS = ("compute", "record_claim", "record_settlement")
CLAIM_STATUSES = ("computed", "packed", "submitted", "partial", "settled", "disputed")


class F2SchemeClaims(BaseAgent):
    """Scheme Claims Agent — F2. Computation is headless; the
    claim pack assembly and submission are supervised (Cowork)."""

    agent_code = "F2"
    agent_name = "Scheme Claims"

    def __init__(self):
        super().__init__()

    def describe(self) -> str:
        return (
            "Samsung scheme claims: parse window/slabs from the verified "
            "scheme register, compute the earned claim from serialised "
            "sales, and drive the claim lifecycle to settlement."
        )

    # ── validation ────────────────────────────────────────────────

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        action = payload.get("action")
        if action not in VALID_ACTIONS:
            return f"'action' must be one of {list(VALID_ACTIONS)}"

        if not payload.get("scheme_id"):
            return "'scheme_id' is required"

        if action == "compute":
            serials = payload.get("serials")
            if not isinstance(serials, list) or not serials:
                return "'serials' must be a non-empty list for compute"
            for i, s in enumerate(serials):
                if not isinstance(s, dict) or not s.get("serial"):
                    return f"serials[{i}]: 'serial' is required"

        if action == "record_claim":
            amount = payload.get("amount_claimed")
            if not isinstance(amount, (int, float)) or isinstance(amount, bool):
                return "'amount_claimed' must be a number"
            if amount <= 0:
                return "'amount_claimed' must be > 0"

        if action == "record_settlement":
            amount = payload.get("amount_received")
            if not isinstance(amount, (int, float)) or isinstance(amount, bool):
                return "'amount_received' must be a number"
            if amount < 0:
                return "'amount_received' must be ≥ 0"

        return None

    # ── execution ─────────────────────────────────────────────────

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        action = payload["action"]

        try:
            if action == "compute":
                return self._compute(payload)
            if action == "record_claim":
                return self._record_claim(payload)
            return self._record_settlement(payload)
        except SchemeWindowError as exc:
            # Bad scheme config (window inversion, slab gap) is the
            # caller's data problem — refused, not FAILED-by-crash.
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Scheme configuration invalid: {exc}",
            )

    # ── compute ───────────────────────────────────────────────────

    def _compute(self, payload: dict) -> AgentResult:
        from datetime import date

        window = payload.get("window") or {}
        try:
            scheme_start = date.fromisoformat(str(window.get("start"))[:10])
            scheme_end = date.fromisoformat(str(window.get("end"))[:10])
        except (ValueError, TypeError):
            return AgentResult(
                status="VALIDATION_ERROR",
                error="'window.start'/'window.end' must be ISO dates (YYYY-MM-DD)",
            )

        eligible = payload.get("eligible_skus")
        if not isinstance(eligible, list) or not eligible:
            return AgentResult(
                status="VALIDATION_ERROR",
                error="'eligible_skus' must be a non-empty list",
            )

        already = payload.get("already_claimed", 0.0)
        if not isinstance(already, (int, float)) or isinstance(already, bool) or already < 0:
            return AgentResult(
                status="VALIDATION_ERROR",
                error="'already_claimed' must be a non-negative number",
            )

        result = scheme_claims.earned_claim(
            scheme_start=scheme_start,
            scheme_end=scheme_end,
            eligible_skus={str(s) for s in eligible},
            serials=payload["serials"],
            already_claimed=float(already),
            slabs=payload.get("slabs"),
        )

        summary = {
            "scheme_id": payload["scheme_id"],
            "eligible_serials": result["eligible_serials"],
            "excluded_count": len(result["excluded_serials"]),
            "qualifying_sales_value": result["qualifying_sales_value"],
            "slab_rate": result["slab_rate"],
            "earned_amount": result["earned_amount"],
            "claimable_amount": result["claimable_amount"],
        }

        logger.info(
            "F2 compute %s: %d eligible, earned %.2f, claimable %.2f",
            payload["scheme_id"], result["eligible_serials"],
            result["earned_amount"], result["claimable_amount"],
        )

        return AgentResult(
            status="SUCCESS",
            output={
                "summary": summary,
                "computation": result,
                "claim_pack_lines": scheme_claims.claim_pack_lines(result),
                "next_step": (
                    "Human verifies the computation, assembles the claim "
                    "pack in Cowork, then runs record_claim with the "
                    "claimed amount"
                ),
            },
            actions_taken=[
                "computed_earned_claim",
                f"evaluated_{len(payload['serials'])}_serials",
                f"excluded_{len(result['excluded_serials'])}_serials",
            ],
        )

    # ── record_claim / record_settlement (lifecycle transitions) ──

    def _record_claim(self, payload: dict) -> AgentResult:
        now = datetime.now(timezone.utc).isoformat()
        amount = float(payload["amount_claimed"])
        computed = payload.get("amount_computed")

        overclaim = (
            computed is not None
            and amount > float(computed) + 0.01
        )

        return AgentResult(
            status="SUCCESS",
            output={
                "claim_id": payload["scheme_id"],
                "transition": "computed→packed/submitted",
                "amount_claimed": amount,
                "amount_computed": computed,
                "overclaim_flag": overclaim,
                "recorded_ts": now,
                "note": (
                    "OVERCLAIM: claimed exceeds computed — reconcile "
                    "before submission" if overclaim
                    else "Claim amount within computation"
                ),
            },
            actions_taken=["recorded_claim", f"amount_{amount:.2f}"],
        )

    def _record_settlement(self, payload: dict) -> AgentResult:
        now = datetime.now(timezone.utc).isoformat()
        received = float(payload["amount_received"])
        claimed = payload.get("amount_claimed")

        variance = (
            None if claimed is None
            else round(received - float(claimed), 2)
        )

        if variance is None:
            status = "settled"
            note = "No claimed amount supplied — recorded as settled"
        elif abs(variance) <= 0.01:
            status = "settled"
            note = "Settled in full"
        elif variance < 0:
            status = "partial"
            note = f"Short-paid by {abs(variance):,.2f} — chase or accept"
        else:
            status = "settled"
            note = f"Over-paid by {variance:,.2f} — verify credit notes"

        return AgentResult(
            status="SUCCESS",
            output={
                "claim_id": payload["scheme_id"],
                "transition": f"→{status}",
                "amount_received": received,
                "variance": variance,
                "variance_reason": payload.get("variance_reason"),
                "status": status,
                "recorded_ts": now,
                "note": note,
            },
            actions_taken=["recorded_settlement", f"status_{status}"],
        )
