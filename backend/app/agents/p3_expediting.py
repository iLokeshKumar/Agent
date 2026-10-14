"""P3 Expediting — vendor follow-up on open POs until goods arrive.

Per the functional spec: "Owns every open PO until goods arrive:
confirms order acceptance, tracks committed dates, chases slippage
early, and keeps requesters and project managers informed."

Autonomy A2 for standard follow-ups (email/WhatsApp chases); every
mitigation with a price tag lands in ``pending_approval`` — the agent
never commits spend, never promises a date, never contacts the MD
(project-critical slippage is flagged *for* a human to escalate).

Payload:
  {
    "pos": [
      {"po_number": str, "vendor": str,
       "committed_date": "YYYY-MM-DD", "today"?: "YYYY-MM-DD",
       "confirmed"?: bool,            # vendor acknowledged the order
       "value"?: number,              # ≥ 0
       "project_milestone"?: bool, "customer_order"?: bool,
       "stockout_risk"?: bool,
       "mitigation"?: {"type": str, "cost"?: number}}
    ]
  }

Output: one row per PO (verdict, escalation step, impact, mitigation
status), a rendered brief for the buyer, and vendor OTIF scorecards
when delivery history is supplied via ``vendor_history``.
"""

import logging
import uuid
from datetime import date

from app.agents.base import BaseAgent, AgentResult
from app.services.expediting import (
    VERDICT_ORDER,
    default_confirm_by,
    days_late as days_late_fn,
    escalation,
    impact,
    mitigation_options,
    otif,
    parse_date,
    verdict,
)

logger = logging.getLogger("agent.P3")


class P3Expediting(BaseAgent):
    """Order expediting & vendor follow-up — chases, never commits."""

    agent_code = "P3"
    agent_name = "Expediting"

    def describe(self) -> str:
        return (
            "Tracks open POs against committed dates, chases vendors "
            "on the escalation ladder, triages delay impact and "
            "proposes (never commits) mitigations."
        )

    # ── validation ────────────────────────────────────────────────

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        pos = payload.get("pos")
        if not isinstance(pos, list) or not pos:
            return "Payload must contain 'pos' as a non-empty list"

        for i, po in enumerate(pos):
            num = po.get("po_number")
            if not num or not isinstance(num, str):
                return f"pos[{i}]: 'po_number' is required"
            if not po.get("vendor") or not isinstance(po.get("vendor"), str):
                return f"pos[{i}]: 'vendor' is required"

            try:
                parse_date(po.get("committed_date"))
            except ValueError as exc:
                return f"pos[{i}]: 'committed_date' — {exc}"

            if po.get("today") is not None:
                try:
                    parse_date(po.get("today"))
                except ValueError as exc:
                    return f"pos[{i}]: 'today' — {exc}"

            confirmed = po.get("confirmed")
            if confirmed is not None and not isinstance(confirmed, bool):
                return f"pos[{i}]: 'confirmed' must be a boolean"

            value = po.get("value")
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return f"pos[{i}]: 'value' must be a number"
                if value < 0:
                    return f"pos[{i}]: 'value' must be ≥ 0"

            for flag in ("project_milestone", "customer_order",
                         "stockout_risk"):
                if flag in po and not isinstance(po[flag], bool):
                    return f"pos[{i}]: '{flag}' must be a boolean"

            mit = po.get("mitigation")
            if mit is not None:
                if not isinstance(mit, dict):
                    return f"pos[{i}]: 'mitigation' must be an object"
                if not mit.get("type") or not isinstance(mit.get("type"), str):
                    return f"pos[{i}]: 'mitigation.type' is required"
                cost = mit.get("cost", 0)
                if (isinstance(cost, bool)
                        or not isinstance(cost, (int, float))):
                    return f"pos[{i}]: 'mitigation.cost' must be a number"

        history = payload.get("vendor_history")
        if history is not None:
            if not isinstance(history, dict):
                return "'vendor_history' must be an object"
            for vendor, rec in history.items():
                if not isinstance(rec, dict):
                    return f"vendor_history[{vendor!r}] must be an object"
                on_time = rec.get("on_time")
                total = rec.get("total")
                if (isinstance(on_time, bool) or not isinstance(on_time, int)
                        or on_time < 0):
                    return (
                        f"vendor_history[{vendor!r}]: "
                        "'on_time' must be an int ≥ 0"
                    )
                if (isinstance(total, bool) or not isinstance(total, int)
                        or total < 0):
                    return (
                        f"vendor_history[{vendor!r}]: "
                        "'total' must be an int ≥ 0"
                    )
                if on_time > total:
                    return (
                        f"vendor_history[{vendor!r}]: "
                        "'on_time' cannot exceed 'total'"
                    )

        return None

    # ── execution ─────────────────────────────────────────────────

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        today = (
            parse_date(payload["today"])
            if payload.get("today") is not None
            else date.today()
        )

        rows = []
        errors = []

        for po in payload["pos"]:
            try:
                rows.append(self._score(po, today))
            except (ValueError, TypeError) as exc:
                errors.append({
                    "po_number": po.get("po_number"),
                    "error": str(exc),
                })

        if errors and not rows:
            return AgentResult(
                status="FAILED",
                error=(
                    "All PO computations failed: "
                    + "; ".join(e["error"] for e in errors)
                ),
                output={"errors": errors},
            )

        summary = {
            "pos_tracked": len(rows) + len(errors),
            "slipped": sum(1 for r in rows if r["verdict"] == "SLIPPED"),
            "unconfirmed": sum(
                1 for r in rows if r["verdict"] == "UNCONFIRMED"
            ),
            "due_soon": sum(1 for r in rows if r["verdict"] == "DUE_SOON"),
            "on_track": sum(1 for r in rows if r["verdict"] == "ON_TRACK"),
            "mitigations_pending_approval": sum(
                1 for r in rows
                if r.get("mitigation")
                and r["mitigation"]["status"] == "pending_approval"
            ),
        }

        output = {
            "po_rows": rows,
            "errors": errors,
            "summary": summary,
            "brief": self._render_brief(rows),
            "next_step": (
                "Send the chase/confirmation messages from the brief; "
                "route 'pending_approval' mitigations to the buyer — "
                "P3 never commits spend"
            ),
        }

        history = payload.get("vendor_history")
        if history:
            output["vendor_scorecards"] = {
                vendor: {
                    "otif": otif(rec["on_time"], rec["total"]),
                    "on_time": rec["on_time"],
                    "total": rec["total"],
                }
                for vendor, rec in history.items()
            }

        return AgentResult(
            status="SUCCESS",
            output=output,
            actions_taken=[
                "scored_open_pos",
                "computed_escalations",
                "rendered_expediting_brief",
            ],
        )

    def _score(self, po: dict, today: date) -> dict:
        committed = parse_date(po["committed_date"])
        late = days_late_fn(committed, today)
        confirmed = po.get("confirmed")
        v = verdict(days_late=late, confirmed=confirmed)
        impact_level = impact(
            days_late=late,
            project_milestone=bool(po.get("project_milestone", False)),
            customer_order=bool(po.get("customer_order", False)),
            stockout_risk=bool(po.get("stockout_risk", False)),
        )

        offered = []
        mit = po.get("mitigation")
        if mit is not None and late > 0:
            offered = [{
                "type": mit["type"],
                "cost": mit.get("cost", 0),
            }]

        options = mitigation_options(
            days_late=late,
            impact_level=impact_level,
            offered=offered,
        )

        return {
            "po_number": po["po_number"],
            "vendor": po["vendor"],
            "committed_date": committed.isoformat(),
            "confirm_by": default_confirm_by(committed).isoformat(),
            "days_late": late,
            "verdict": v,
            "escalation": escalation(
                days_late=late, confirmed=confirmed,
            ),
            "impact": impact_level,
            "mitigation": options[0] if options else None,
            "mitigation_options": options,
        }

    def _render_brief(self, rows: list[dict]) -> str:
        """Text brief for the buyer — worst verdict first."""
        order = {v: i for i, v in enumerate(VERDICT_ORDER)}
        ranked = sorted(rows, key=lambda r: order.get(r["verdict"], 9))

        lines = ["EXPEDITING BRIEF", "================"]
        for r in ranked:
            lateness = (
                f"{r['days_late']}d late"
                if r["days_late"] > 0
                else f"due {r['committed_date']}"
            )
            line = (
                f"[{r['verdict']}] {r['po_number']} — {r['vendor']}: "
                f"{lateness}; next: {r['escalation'] or 'none'}; "
                f"impact {r['impact']}"
            )
            if r.get("mitigation"):
                line += (
                    f"; mitigation {r['mitigation']['type']} "
                    f"({r['mitigation']['status']})"
                )
            lines.append(line)
        return "\n".join(lines)
