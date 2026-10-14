"""S2 Stock Health — weekly ageing/EOL scoring across serialised stock.

Per the build matrix: "Ageing/EOL scoring job (code) into sku_health;
brief template". The scoring is deterministic code in
app/services/stock_health.py — shared with the nightly mart builder so
agent output and mart rows can never disagree. S2 is read-only: it
renders the brief and flags issues; it writes no business objects.

Payload:
  {
    "skus": [
      {"sku_ref": str, "branch"?: str,
       "ages_days": [int,...],     # days since receipt, one per unit
       "weekly_units": [n,...],    # offtake history, oldest first
       "eol"?: bool}
    ]
  }

Output: one health row per SKU+branch, a rendered text brief, and a
summary counting recommendations. Reorder checks hand off to S3 —
S2 never raises purchase requests itself.
"""

import logging
import uuid

from app.agents.base import BaseAgent, AgentResult
from app.services.stock_health import health_record

logger = logging.getLogger("agent.S2")


class S2StockHealth(BaseAgent):
    """Weekly ageing/EOL scoring — read-only, deterministic."""

    agent_code = "S2"
    agent_name = "Stock Health"

    def describe(self) -> str:
        return (
            "Weekly stock-health brief: ageing buckets, EOL flags and "
            "action recommendations per SKU+branch; feeds S3."
        )

    # ── validation ────────────────────────────────────────────────

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        skus = payload.get("skus")
        if not isinstance(skus, list) or not skus:
            return "Payload must contain 'skus' as a non-empty list"

        for i, sku in enumerate(skus):
            if not sku.get("sku_ref") or not isinstance(
                sku.get("sku_ref"), str
            ):
                return f"skus[{i}]: 'sku_ref' is required"

            ages = sku.get("ages_days")
            if not isinstance(ages, list):
                return f"skus[{i}]: 'ages_days' must be a list"

            weekly = sku.get("weekly_units")
            if not isinstance(weekly, list):
                return f"skus[{i}]: 'weekly_units' must be a list"

            for j, age in enumerate(ages):
                if (not isinstance(age, int) or isinstance(age, bool)
                        or age < 0):
                    return (
                        f"skus[{i}].ages_days[{j}]: "
                        "ages must be integers ≥ 0"
                    )
            for j, u in enumerate(weekly):
                # NaN/inf pass naive ``x < 0`` checks (all comparisons
                # on NaN are False) — reject non-finite floats here so
                # the contract fails closed, not FAILED mid-execute.
                if (not isinstance(u, (int, float)) or isinstance(u, bool)
                        or u != u or u in (float("inf"), float("-inf"))
                        or u < 0):
                    return (
                        f"skus[{i}].weekly_units[{j}]: "
                        "units must be finite numbers ≥ 0"
                    )

            # eol is a business flag that forces clearance_push — it
            # must be a real boolean. Non-bool values ("false", 0,
            # null) are rejected rather than coerced, or any payload
            # could flip a SKU into (or out of) the clearance lane.
            eol = sku.get("eol", False)
            if not isinstance(eol, bool):
                return f"skus[{i}]: 'eol' must be a boolean"

            branch = sku.get("branch")
            if branch is not None and (
                not isinstance(branch, str) or not branch.strip()
            ):
                return f"skus[{i}]: 'branch' must be a non-empty string"

        return None

    # ── execution ─────────────────────────────────────────────────

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        rows = []
        errors = []

        for sku in payload["skus"]:
            try:
                rows.append(health_record(
                    sku_ref=sku["sku_ref"],
                    branch=sku.get("branch") or "HO",
                    ages_days=sku["ages_days"],
                    weekly_units=sku["weekly_units"],
                    eol=sku.get("eol", False),
                    period=str(sku.get("period") or ""),
                ))
            except (ValueError, TypeError) as exc:
                errors.append({"sku_ref": sku.get("sku_ref"), "error": str(exc)})

        if errors and not rows:
            return AgentResult(
                status="FAILED",
                error=(
                    "All SKU computations failed: "
                    + "; ".join(e["error"] for e in errors)
                ),
                output={"errors": errors},
            )

        summary = {
            "skus_evaluated": len(rows) + len(errors),
            "clearance_push": sum(
                1 for r in rows if r["action_reco"] == "clearance_push"
            ),
            "ageing_review": sum(
                1 for r in rows if r["action_reco"] == "ageing_review"
            ),
            "reorder_check": sum(
                1 for r in rows if r["action_reco"] == "reorder_check"
            ),
            "healthy": sum(
                1 for r in rows if r["action_reco"] == "healthy"
            ),
        }

        return AgentResult(
            status="SUCCESS",
            output={
                "health_rows": rows,
                "errors": errors,
                "summary": summary,
                "brief": self._render_brief(rows),
                "next_step": (
                    "Route 'reorder_check' SKUs to S3 for reorder-point "
                    "proposals; 'clearance_push' to the sales owner"
                ),
            },
            actions_taken=["scored_stock_health", "rendered_brief"],
        )

    def _render_brief(self, rows: list[dict]) -> str:
        """Text brief for the stores owner — worst first."""
        order = {"clearance_push": 0, "ageing_review": 1,
                 "reorder_check": 2, "healthy": 3}
        ranked = sorted(rows, key=lambda r: order.get(r["action_reco"], 9))

        lines = ["STOCK HEALTH BRIEF", "=================="]
        for r in ranked:
            cover = (
                f"{r['weeks_cover']}w cover"
                if r["weeks_cover"] is not None else "no offtake"
            )
            lines.append(
                f"[{r['action_reco'].upper()}] {r['sku_ref']} @ "
                f"{r['branch']}: {r['on_hand']} on hand, {cover}"
                + (" — EOL" if r["eol_flag"] else "")
            )
        return "\n".join(lines)
