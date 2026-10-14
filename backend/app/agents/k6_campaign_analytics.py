"""K6 Campaign Analytics — campaign performance KPI computation and reporting.

Autonomy: A1 (read-only analytics — no writes except persisting KPI snapshot
to Campaign.kpi_json).

Actions:
  compute  — calculate campaign KPIs and persist to Campaign.kpi_json
  report   — return KPI summary for a campaign or all closed campaigns
"""

import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"compute", "report"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}


class K6CampaignAnalytics(BaseAgent):
    """Campaign Analytics — K6

    Computes performance KPIs for completed/live campaigns:
    - delivery_rate: sent / total_audience
    - response_rate: responded / sent
    - conversion_rate: converted / responded
    - hot_lead_rate: (interested + callback + converted) / sent
    - Persists snapshot to Campaign.kpi_json for D1 reporting
    """

    agent_code = "K6"
    agent_name = "Campaign Analytics"

    def describe(self) -> str:
        return "Campaign KPI computation: delivery, response, conversion, and hot-lead rates."

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
        if action == "compute" and not payload.get("campaign_id"):
            return "'campaign_id' is required for compute"
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
        from app.models.enterprise import Campaign, CampaignContact

        action = payload["action"]
        entity = payload.get("entity", "YEXIS-EL")
        now = datetime.now(timezone.utc)

        def _compute_kpis(campaign: Campaign) -> dict:
            contacts = db.query(CampaignContact).filter(CampaignContact.campaign_id == campaign.id).all()
            total = len(contacts)
            sent = sum(1 for c in contacts if c.message_sent_ts)
            responded = sum(1 for c in contacts if c.response_ts)  # every reply, classified or not
            converted = sum(1 for c in contacts if c.response_type == "converted")
            hot = sum(1 for c in contacts if c.response_type in ("interested", "callback", "converted"))

            def _pct(num: int, den: int) -> float:
                return round(num / den * 100, 1) if den > 0 else 0.0

            return {
                "computed_ts": now.isoformat(),
                "total_audience": total,
                "messages_sent": sent,
                "responses": responded,
                "converted": converted,
                "hot_leads": hot,
                "delivery_rate_pct": _pct(sent, total),
                "response_rate_pct": _pct(responded, sent),
                "conversion_rate_pct": _pct(converted, responded),
                "hot_lead_rate_pct": _pct(hot, sent),
                **_intel(campaign),
            }

        def _intel(campaign: Campaign) -> dict:
            from app.services.campaign_intel import contact_scores, variant_stats
            scores = contact_scores(db, campaign).values()
            tiers = {"hot": 0, "warm": 0, "cold": 0, "not_interested": 0}
            for r in scores:
                tiers[r["tier"]] += 1
            out = {
                "engagement_tiers": tiers,
                "unique_opens": sum(1 for r in scores if r["opens"]),
                "unique_clicks": sum(1 for r in scores if r["clicks"]),
                "unsubscribed": None,
            }
            from app.models.enterprise import ContactOptOut
            out["unsubscribed"] = db.query(ContactOptOut).filter(
                ContactOptOut.campaign_id == str(campaign.id)).count()
            ab = campaign.ab_test_json or {}
            if ab.get("variants"):
                out["ab_test"] = {
                    "winner": ab.get("winner"), "basis": ab.get("basis"),
                    "decided_at": ab.get("decided_at"), "variants": variant_stats(db, campaign),
                }
            return out

        if action == "compute":
            campaign_id = payload["campaign_id"]
            campaign = db.get(Campaign, campaign_id)
            if campaign is None:
                return AgentResult(status="VALIDATION_ERROR", error=f"Campaign '{campaign_id}' not found")

            kpis = _compute_kpis(campaign)
            campaign.kpi_json = kpis
            db.commit()

            return AgentResult(
                status="SUCCESS",
                output={"campaign_id": campaign_id, "campaign_name": campaign.name, **kpis},
                actions_taken=["campaign_kpis_computed"],
            )

        if action == "report":
            campaign_id = payload.get("campaign_id")
            if campaign_id:
                campaign = db.get(Campaign, campaign_id)
                if campaign is None:
                    return AgentResult(status="VALIDATION_ERROR", error=f"Campaign '{campaign_id}' not found")
                kpis = campaign.kpi_json or _compute_kpis(campaign)
                return AgentResult(
                    status="SUCCESS",
                    output={"campaign_id": campaign_id, "campaign_name": campaign.name, **kpis},
                    actions_taken=["campaign_analytics_report"],
                )

            campaigns = db.query(Campaign).filter(
                Campaign.entity == entity,
                Campaign.status.in_(["broadcast", "tracking", "closed"]),
            ).all()
            rows = []
            for c in campaigns:
                kpis = c.kpi_json or {}
                rows.append({
                    "campaign_id": str(c.id),
                    "name": c.name,
                    "type": c.campaign_type,
                    "status": c.status,
                    "delivery_rate_pct": kpis.get("delivery_rate_pct", 0),
                    "response_rate_pct": kpis.get("response_rate_pct", 0),
                    "hot_lead_rate_pct": kpis.get("hot_lead_rate_pct", 0),
                })
            return AgentResult(
                status="SUCCESS",
                output={"entity": entity, "campaigns": rows},
                actions_taken=["portfolio_analytics_report"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
