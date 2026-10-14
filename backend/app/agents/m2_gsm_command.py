"""M2 GSM Command — General/Senior Sales Manager territory command centre.

Autonomy: A1 (read-only — territory and entity-wide intelligence for senior
leadership; no writes to any business document).

Actions:
  territory  — entity-wide dealer scorecard and branch summary
  scorecard  — detailed dealer card (DealerMart + KPI data)
  kpis       — entity KPI dashboard from KpiSnapshot
  topline    — executive topline: revenue, outstanding, DSO, forecast
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import or_

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"territory", "scorecard", "kpis", "topline"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}


class M2GsmCommand(BaseAgent):
    """GSM Command — M2

    Senior Sales Manager / GM territory command centre:
    - Entity-wide and branch-level dealer portfolio view
    - KPI dashboard (20 KPIs from KpiSnapshot)
    - Executive topline linking AR, DSO, forecast, and stock availability
    - Dealer scorecard for individual deep-dives
    - A1 only: all outputs advisory; GM takes action via other agents or direct channels
    """

    agent_code = "M2"
    agent_name = "GSM Command"

    def describe(self) -> str:
        return "Senior Sales Manager command centre — territory, scorecards, KPIs, and topline."

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
        if action == "scorecard" and not payload.get("dealer_ref"):
            return "'dealer_ref' is required for scorecard action"
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
        today = date.today()

        if action == "territory":
            dealers = db.query(DealerMart).filter(
                ~DealerMart.dealer_ref.like("DEMO-%")
            ).all()
            branches: dict[str, dict] = {}
            for d in dealers:
                br = d.branch or "Unknown"
                if br not in branches:
                    branches[br] = {"dealer_count": 0, "total_outstanding": 0.0, "overdue_count": 0}
                branches[br]["dealer_count"] += 1
                branches[br]["total_outstanding"] += d.outstanding_amt or 0
                if d.overdue_buckets_json and any(
                    (d.overdue_buckets_json.get(b) or 0) > 0
                    for b in ("d31_60", "d61_90", "over_90")
                ):
                    branches[br]["overdue_count"] += 1

            for br in branches:
                branches[br]["total_outstanding"] = round(branches[br]["total_outstanding"], 2)

            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "as_of": str(today),
                    "total_dealers": len(dealers),
                    "total_outstanding": round(sum(d.outstanding_amt or 0 for d in dealers), 2),
                    "branches": branches,
                },
                actions_taken=["territory_report_generated"],
            )

        if action == "scorecard":
            dealer_ref = payload["dealer_ref"]
            dealer = db.query(DealerMart).filter(DealerMart.dealer_ref == dealer_ref).first()
            if dealer is None:
                return AgentResult(status="VALIDATION_ERROR", error=f"Dealer '{dealer_ref}' not found in DealerMart")

            return AgentResult(
                status="SUCCESS",
                output={
                    "dealer_ref": dealer_ref,
                    "branch": dealer.branch,
                    "outstanding_amt": dealer.outstanding_amt,
                    "overdue_buckets": dealer.overdue_buckets_json,
                    "payment_behavior_score": dealer.payment_behavior_score,
                    "last_order_ts": dealer.last_order_ts.strftime("%Y-%m-%d %H:%M") if dealer.last_order_ts else None,
                    "offtake_13wk": dealer.offtake_13wk_json,
                    "scheme_eligibility": dealer.scheme_eligibility_json,
                    "stock_opportunity": dealer.stock_opportunity_json,
                    "refreshed_ts": dealer.refreshed_ts.strftime("%Y-%m-%d") if dealer.refreshed_ts else None,
                },
                actions_taken=["dealer_scorecard_generated"],
            )

        if action == "kpis":
            kpi_ids = payload.get("kpi_ids") or [
                "dso_days", "promise_reliability", "p4_auto_match_rate",
                "overdue_receivables", "stock_availability_pct", "vendor_otif_pct",
                "time_to_fill_days", "amc_renewal_rate_pct", "cash_forecast_accuracy",
            ]
            rows = (
                db.query(KpiSnapshot)
                .filter(
                    KpiSnapshot.entity == entity,
                    KpiSnapshot.kpi_id.in_(kpi_ids),
                    or_(
                        KpiSnapshot.source_lane.is_(None),
                        KpiSnapshot.source_lane != "demo",
                    ),
                )
                .order_by(KpiSnapshot.period.desc())
                .all()
            )
            # Latest value per KPI
            seen = set()
            kpis = []
            for r in rows:
                if r.kpi_id not in seen:
                    seen.add(r.kpi_id)
                    kpis.append({
                        "kpi_id": r.kpi_id,
                        "value": r.value,
                        "target": r.target,
                        "period": str(r.period),
                        "status": (
                            "ON_TARGET" if r.value is not None and r.target is not None and r.value <= r.target
                            else "ABOVE_TARGET" if r.value is not None and r.target is not None
                            else "NO_DATA"
                        ),
                    })
            return AgentResult(
                status="SUCCESS",
                output={"entity": entity, "kpis": kpis, "as_of": str(today)},
                actions_taken=["kpi_dashboard_generated"],
            )

        if action == "topline":
            def _latest_kpi(kpi_id: str) -> float | None:
                row = (
                    db.query(KpiSnapshot)
                    .filter(
                        KpiSnapshot.kpi_id == kpi_id,
                        KpiSnapshot.entity == entity,
                        or_(
                            KpiSnapshot.source_lane.is_(None),
                            KpiSnapshot.source_lane != "demo",
                        ),
                    )
                    .order_by(KpiSnapshot.period.desc())
                    .first()
                )
                return row.value if row else None

            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "as_of": str(today),
                    "total_open_receivables": _latest_kpi("total_open_receivables"),
                    "overdue_receivables": _latest_kpi("overdue_receivables"),
                    "dso_days": _latest_kpi("dso_days"),
                    "dso_target": 42,
                    "stock_availability_pct": _latest_kpi("stock_availability_pct"),
                    "vendor_otif_pct": _latest_kpi("vendor_otif_pct"),
                    "cash_forecast_accuracy": _latest_kpi("cash_forecast_accuracy"),
                    "amc_renewal_rate_pct": _latest_kpi("amc_renewal_rate_pct"),
                },
                actions_taken=["executive_topline_generated"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
