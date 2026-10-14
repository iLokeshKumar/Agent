"""S3 Reorder — weekly reorder proposals from serialised stock.

Per the build matrix: "Reorder-point statistics job (code,
property-tested); feeds P1". The judgement (approve the proposed PO)
stays human/P1 — S3 only proposes. The math is deterministic code in
app/services/reorder.py; this agent is I/O + rendering around it.

Payload:
  {
    "sku_demands": [
      {"sku_ref": str, "branch"?: str,
       "weekly_units": [n,...],          # offtake history (oldest first)
       "on_hand": int,
       "lead_time_weeks": float,
       "safety_stock"?: int,
       "target_cover_weeks"?: float,
       "min_order_qty"?: int}
    ]
  }

Output: one proposal per SKU — needs_reorder, reorder_point,
recommended_qty — ready to hand to P1 as a purchase_request payload.
"""

import logging
import uuid

from app.agents.base import BaseAgent, AgentResult
from app.services.reorder import (
    average_weekly_demand,
    needs_reorder,
    order_quantity,
    reorder_point,
)

logger = logging.getLogger("agent.S3")


class S3Reorder(BaseAgent):
    """Weekly reorder-point proposals — deterministic, propose-only."""

    agent_code = "S3"
    agent_name = "Reorder"

    def describe(self) -> str:
        return (
            "Weekly reorder proposals from offtake history and "
            "lead times, computed deterministically; feeds P1."
        )

    # ── validation ────────────────────────────────────────────────

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        items = payload.get("sku_demands")
        if not isinstance(items, list) or not items:
            return "Payload must contain 'sku_demands' as a non-empty list"

        for i, item in enumerate(items):
            ref = item.get("sku_ref")
            if not ref or not isinstance(ref, str):
                return f"sku_demands[{i}]: 'sku_ref' is required"

            weekly = item.get("weekly_units")
            if not isinstance(weekly, list):
                return f"sku_demands[{i}]: 'weekly_units' must be a list"

            on_hand = item.get("on_hand")
            if (not isinstance(on_hand, int) or isinstance(on_hand, bool)
                    or on_hand < 0):
                return f"sku_demands[{i}]: 'on_hand' must be an int ≥ 0"

            lt = item.get("lead_time_weeks")
            if (not isinstance(lt, (int, float)) or isinstance(lt, bool)
                    or lt != lt or lt in (float("inf"), float("-inf")) or lt < 0):
                return f"sku_demands[{i}]: 'lead_time_weeks' must be ≥ 0"

            for j, u in enumerate(weekly):
                # NaN/inf pass naive ``x < 0`` checks — reject non-finite
                # floats here so the contract fails closed.
                if (not isinstance(u, (int, float)) or isinstance(u, bool)
                        or u != u or u in (float("inf"), float("-inf"))
                        or u < 0):
                    return (
                        f"sku_demands[{i}].weekly_units[{j}]: "
                        "units must be finite numbers ≥ 0"
                    )

            for extra in ("safety_stock", "target_cover_weeks",
                          "min_order_qty"):
                if extra in item and item[extra] is not None:
                    v = item[extra]
                    if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                        return (
                            f"sku_demands[{i}]: '{extra}' must be ≥ 0"
                        )

        return None

    # ── execution ─────────────────────────────────────────────────

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        proposals = []
        errors = []

        for item in payload["sku_demands"]:
            ref = item["sku_ref"]
            try:
                proposal = self._propose(item)
                proposals.append(proposal)
            except (ValueError, TypeError) as exc:
                errors.append({"sku_ref": ref, "error": str(exc)})

        if errors and not proposals:
            return AgentResult(
                status="FAILED",
                error=(
                    "All SKU computations failed: "
                    + "; ".join(e["error"] for e in errors)
                ),
                output={"errors": errors},
            )

        actionable = [p for p in proposals if p["needs_reorder"]]

        return AgentResult(
            status="SUCCESS",
            output={
                "proposals": proposals,
                "errors": errors,
                "summary": {
                    "skus_evaluated": len(proposals) + len(errors),
                    "needs_reorder": len(actionable),
                    "total_recommended_qty": sum(
                        p["recommended_qty"] for p in actionable
                    ),
                },
                "next_step": (
                    "Route actionable proposals to P1 as purchase_request "
                    "payloads for human approval" if actionable else None
                ),
            },
            actions_taken=["computed_reorder_points", "rendered_proposals"],
        )

    def _propose(self, item: dict) -> dict:
        """Compute one SKU's reorder proposal. Raises ValueError on bad math input."""
        min_order_qty = int(item.get("min_order_qty") or 0)
        avg_weekly = average_weekly_demand(item["weekly_units"])
        rop = reorder_point(
            avg_weekly=avg_weekly,
            lead_time_weeks=float(item["lead_time_weeks"]),
            safety_stock=float(item.get("safety_stock") or 0),
        )
        qty = order_quantity(
            on_hand=item["on_hand"],
            rop=rop,
            avg_weekly=avg_weekly,
            target_cover_weeks=float(
                item.get("target_cover_weeks") or 4.0
            ),
            min_order_qty=min_order_qty,
        )

        return {
            "sku_ref": item["sku_ref"],
            "branch": item.get("branch") or "HO",
            "avg_weekly_demand": round(avg_weekly, 4),
            "on_hand": item["on_hand"],
            "reorder_point": rop,
            "needs_reorder": needs_reorder(item["on_hand"], rop),
            "recommended_qty": qty,
            "min_order_qty_applied": min_order_qty > 0 and qty == min_order_qty,
        }
