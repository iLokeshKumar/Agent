"""F5 Cash Forecast — 13-week rolling cash position.

Builds a deterministic forward view from:
  - AR inflow: DealerMart outstanding × promise reliability score
  - AP outflow: open invoice cache (vendor payables) spread over lead time
  - AR aging buckets → weekly collection probability decay

No LLM math — all arithmetic is Decimal, deterministic, and auditable.

Payload:
  entity          "YEXIS-EL" | "YEXIS-SL"
  horizon_weeks   int 1-13 (default 13)
  as_of_date      YYYY-MM-DD (optional)
  action          "forecast" | "report" | "update_actuals"
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.agents.base import BaseAgent, AgentResult

VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}
VALID_ACTIONS = {"forecast", "report", "update_actuals"}

# Collection probability by aging bucket (conservative assumptions)
BUCKET_WEEKLY_COLLECT_PROB = {
    "current":   Decimal("0.35"),  # 35 % of current AR collected per week
    "d1_30":     Decimal("0.20"),
    "d31_60":    Decimal("0.10"),
    "d61_90":    Decimal("0.05"),
    "over_90":   Decimal("0.02"),
}


class F5CashForecast(BaseAgent):
    """Cash Forecast Agent — F5

    13-week rolling AR inflow / AP outflow forecast:
    - Inflow: DealerMart AR aging × collection probability × promise reliability
    - Outflow: open vendor payables spread evenly over 4-week lead time
    - Net position: cumulative opening balance + inflows - outflows
    """

    agent_code = "F5"
    agent_name = "Cash Forecast"

    def describe(self) -> str:
        return "13-week rolling cash position forecast from AR and AP data."

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"
        entity = payload.get("entity")
        if not entity:
            return "'entity' is required"
        if entity not in VALID_ENTITIES:
            return f"entity must be one of {sorted(VALID_ENTITIES)}"
        action = payload.get("action", "forecast")
        if action not in VALID_ACTIONS:
            return f"action must be one of {sorted(VALID_ACTIONS)}"
        horizon = payload.get("horizon_weeks", 13)
        try:
            h = int(horizon)
        except (TypeError, ValueError):
            return "horizon_weeks must be an integer"
        if not (1 <= h <= 13):
            return "horizon_weeks must be between 1 and 13"
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
        from app.models.enterprise import CashForecast, DealerMart, KpiSnapshot
        from app.sync.cache import SyncCache

        entity = payload["entity"]
        horizon = int(payload.get("horizon_weeks", 13))
        action = payload.get("action", "forecast")
        as_of_str = payload.get("as_of_date")
        try:
            as_of = date.fromisoformat(as_of_str) if as_of_str else date.today()
        except ValueError:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"as_of_date '{as_of_str}' is not a valid date",
            )

        if action == "report":
            return self._report(db, entity, as_of)

        cache = SyncCache()

        # ── AR inflow: from DealerMart aging buckets ──────────────────
        dealer_rows = (
            db.query(DealerMart)
            .filter(DealerMart.branch.isnot(None))
            .all()
        )

        # Promise reliability multiplier from KPI snapshot
        prom_row = (
            db.query(KpiSnapshot)
            .filter(
                KpiSnapshot.kpi_id == "promise_reliability",
                KpiSnapshot.entity == entity,
            )
            .order_by(KpiSnapshot.period.desc())
            .first()
        )
        prom_mult = Decimal(str(prom_row.value)) if (prom_row and prom_row.value) else Decimal("0.7")

        # Sum AR by bucket across all dealers
        bucket_totals: dict[str, Decimal] = {b: Decimal("0") for b in BUCKET_WEEKLY_COLLECT_PROB}
        for dm in dealer_rows:
            buckets = dm.overdue_buckets_json or {}
            for bucket_key in bucket_totals:
                bucket_totals[bucket_key] += Decimal(str(buckets.get(bucket_key, 0) or 0))

        # Weekly inflow = sum over buckets (total × weekly_prob × prom_mult)
        # Apply a decay: bucket_total reduces each week as collections come in
        weekly_inflows = []
        remaining = {k: v for k, v in bucket_totals.items()}
        for week in range(horizon):
            inflow = Decimal("0")
            for bucket, prob in BUCKET_WEEKLY_COLLECT_PROB.items():
                collected = (remaining[bucket] * prob * prom_mult).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                inflow += collected
                remaining[bucket] -= collected
                if remaining[bucket] < 0:
                    remaining[bucket] = Decimal("0")
            weekly_inflows.append(inflow)

        # ── AP outflow: from invoice cache (vendor payables) ──────────
        invoices = cache.get_data("invoices")
        total_payables = Decimal("0")
        for inv in invoices:
            # Only AP invoices (bills) have a non-zero balance here
            balance = Decimal(str(inv.get("balance") or 0))
            if balance > 0:
                total_payables += balance

        # Spread AP evenly over first 4 weeks
        weekly_ap = (total_payables / 4).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) if total_payables > 0 else Decimal("0")
        weekly_outflows = [
            weekly_ap if week < 4 else Decimal("0") for week in range(horizon)
        ]

        # ── Build forecast table ──────────────────────────────────────
        opening_balance = Decimal(str(payload.get("opening_balance", 0) or 0))
        running = opening_balance
        forecast_rows = []
        week_start = as_of

        for i in range(horizon):
            inflow = weekly_inflows[i]
            outflow = weekly_outflows[i]
            net = inflow - outflow
            running += net

            row = {
                "week": i,
                "week_starting": str(week_start),
                "predicted_inflow": float(inflow),
                "predicted_outflow": float(outflow),
                "net": float(net),
                "running_balance": float(running),
                "confidence_pct": float(
                    prom_mult * Decimal("100") * (1 - Decimal(str(i)) / Decimal("20"))
                ),
            }
            forecast_rows.append(row)

            # Persist to CashForecast table
            db.merge(CashForecast(
                entity=entity,
                period=as_of,
                week_offset=i,
                predicted_inflow=float(inflow),
                predicted_outflow=float(outflow),
                net_position=float(running),
                confidence_pct=row["confidence_pct"],
            ))

            week_start += timedelta(weeks=1)

        db.commit()

        output = {
            "entity": entity,
            "as_of_date": str(as_of),
            "horizon_weeks": horizon,
            "opening_balance": float(opening_balance),
            "promise_reliability_used": float(prom_mult),
            "total_ar_pool": float(sum(bucket_totals.values())),
            "total_ap_pool": float(total_payables),
            "forecast": forecast_rows,
            "week_1_net": float(weekly_inflows[0] - weekly_outflows[0]) if forecast_rows else 0,
            "13_week_cumulative_net": float(running - opening_balance),
        }

        self.logger.info(
            "F5 forecast %s %s: AR pool %.0f, AP pool %.0f, 13w net %.0f",
            entity, as_of,
            float(sum(bucket_totals.values())),
            float(total_payables),
            float(running - opening_balance),
        )

        return AgentResult(
            status="SUCCESS",
            output=output,
            actions_taken=[
                "computed_ar_inflow_forecast",
                "computed_ap_outflow_forecast",
                f"persisted_{horizon}_week_forecast",
            ],
        )

    def _report(self, db, entity: str, as_of: date) -> AgentResult:
        from app.models.enterprise import CashForecast

        rows = (
            db.query(CashForecast)
            .filter(CashForecast.entity == entity, CashForecast.period == as_of)
            .order_by(CashForecast.week_offset)
            .all()
        )
        if not rows:
            return AgentResult(
                status="SUCCESS",
                output={"message": f"No forecast for {entity} on {as_of}. Run action=forecast first."},
                actions_taken=["report_generated"],
            )

        return AgentResult(
            status="SUCCESS",
            output={
                "entity": entity,
                "period": str(as_of),
                "weeks": [
                    {
                        "week": r.week_offset,
                        "inflow": r.predicted_inflow,
                        "outflow": r.predicted_outflow,
                        "net_position": r.net_position,
                        "confidence_pct": r.confidence_pct,
                    }
                    for r in rows
                ],
            },
            actions_taken=["report_generated"],
        )
