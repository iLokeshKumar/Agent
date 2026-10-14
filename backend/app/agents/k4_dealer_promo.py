"""K4 Dealer Promo — dealer-specific promotion eligibility and incentive computation.

Autonomy: A1 (propose only — computes eligibility and incentive amounts;
Finance/Marketing Manager approves disbursement).

Actions:
  compute  — calculate promo eligibility and incentive for enrolled dealers
  report   — eligibility summary for a campaign
"""

import uuid
from decimal import Decimal, ROUND_HALF_UP

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"compute", "report"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}

# Incentive slab based on outstanding-payment behaviour score
PROMO_SLABS = [
    (90, Decimal("0.03")),   # score ≥ 90 → 3% of outstanding
    (70, Decimal("0.02")),   # score ≥ 70 → 2%
    (50, Decimal("0.01")),   # score ≥ 50 → 1%
    (0,  Decimal("0.00")),   # below 50  → ineligible
]


def _promo_rate(score: float | None) -> Decimal:
    if score is None:
        return Decimal("0.00")
    for threshold, rate in PROMO_SLABS:
        if score >= threshold:
            return rate
    return Decimal("0.00")


class K4DealerPromo(BaseAgent):
    """Dealer Promo Agent — K4

    Computes dealer-specific promotional incentives from DealerMart data:
    - Uses payment_behavior_score to determine incentive slab rate
    - Applied to offtake value for the campaign SKU basket
    - Proposals only — Finance approves disbursement
    """

    agent_code = "K4"
    agent_name = "Dealer Promo"

    def describe(self) -> str:
        return "Dealer promo eligibility and incentive computation from DealerMart scores."

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"
        action = payload.get("action")
        if not action:
            return "'action' is required"
        if action not in VALID_ACTIONS:
            return f"action must be one of {sorted(VALID_ACTIONS)}"
        if not payload.get("campaign_id"):
            return "'campaign_id' is required"
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
        from app.models.enterprise import Campaign, CampaignContact, DealerMart

        action = payload["action"]
        campaign_id = payload["campaign_id"]
        entity = payload.get("entity", "YEXIS-EL")

        campaign = db.get(Campaign, campaign_id)
        if campaign is None:
            return AgentResult(status="VALIDATION_ERROR", error=f"Campaign '{campaign_id}' not found")

        if action == "compute":
            contacts = db.query(CampaignContact).filter(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.contact_type == "dealer",
            ).all()
            if not contacts:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error="No dealer contacts in campaign. Run K2 audience build first.",
                )

            dealer_refs = [c.contact_ref for c in contacts]
            dealer_rows = {
                d.dealer_ref: d for d in
                db.query(DealerMart).filter(DealerMart.dealer_ref.in_(dealer_refs)).all()
            }

            # Target SKUs from the campaign brief (empty list = all SKUs)
            target_skus: list[str] = (campaign.target_skus_json or {}).get("skus", [])

            eligible = []
            ineligible = []
            no_offtake = []
            total_incentive = Decimal("0")

            for contact in contacts:
                dm = dealer_rows.get(contact.contact_ref)
                score = dm.payment_behavior_score if dm else None
                rate = _promo_rate(score)

                # Derive offtake base from real 13-week sales data in DealerMart.
                # Fall back to outstanding_amt only when the mart hasn't been
                # populated yet (first run before nightly mart completes).
                offtake_json: dict = (dm.offtake_13wk_json or {}) if dm else {}
                if offtake_json:
                    sku_data: dict = offtake_json.get("skus", {})
                    if target_skus:
                        basket_value = sum(
                            sku_data.get(s, {}).get("value", 0.0)
                            for s in target_skus
                        )
                    else:
                        basket_value = offtake_json.get("total_value", 0.0)
                    base_value = Decimal(str(basket_value))
                    base_source = "offtake_13wk"
                else:
                    # No offtake data yet — fall back to outstanding as proxy
                    base_value = Decimal(str(dm.outstanding_amt or 0)) if dm else Decimal("0")
                    base_source = "outstanding_fallback"
                    if dm:
                        no_offtake.append(contact.contact_ref)

                incentive = (base_value * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                entry = {
                    "dealer_ref": contact.contact_ref,
                    "payment_score": score,
                    "incentive_rate_pct": float(rate * 100),
                    "base_value": float(base_value),
                    "base_source": base_source,
                    "incentive_amount": float(incentive),
                }
                if rate > 0:
                    eligible.append(entry)
                    total_incentive += incentive
                else:
                    ineligible.append({
                        "dealer_ref": contact.contact_ref,
                        "payment_score": score,
                        "reason": "score below 50",
                    })

            return AgentResult(
                status="SUCCESS",
                output={
                    "campaign_id": campaign_id,
                    "campaign_name": campaign.name,
                    "entity": entity,
                    "target_skus": target_skus or "all",
                    "eligible_dealers": len(eligible),
                    "ineligible_dealers": len(ineligible),
                    "dealers_missing_offtake": len(no_offtake),
                    "total_incentive_proposed": float(total_incentive),
                    "eligible": eligible,
                    "ineligible": ineligible,
                    "autonomy": "A1 — proposals only. Finance Manager approves disbursement.",
                },
                actions_taken=["promo_eligibility_computed"],
            )

        if action == "report":
            contacts = db.query(CampaignContact).filter(CampaignContact.campaign_id == campaign.id).all()
            return AgentResult(
                status="SUCCESS",
                output={
                    "campaign_id": campaign_id,
                    "campaign_name": campaign.name,
                    "total_enrolled": len(contacts),
                    "dealers": sum(1 for c in contacts if c.contact_type == "dealer"),
                },
                actions_taken=["dealer_promo_report_generated"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
