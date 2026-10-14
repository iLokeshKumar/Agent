"""C1 Indent Planning — Samsung replenishment indent generation.

Autonomy: A1 (propose only — indent suggestions require SCM Manager approval
before submission to Samsung).

Actions:
  plan    — generate indent suggestions from SkuHealth weeks-cover data
  submit  — change suggested indents to submitted status (SCM Manager gate)
  report  — current indent pipeline
"""

import uuid
from datetime import date, datetime, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"plan", "submit", "report"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}

# Weeks-cover thresholds
REORDER_WEEKS_COVER = 4.0    # trigger indent when cover < 4 weeks
TARGET_WEEKS_COVER = 12.0    # target 12 weeks of stock post-replenishment


class C1IndentPlanning(BaseAgent):
    """Indent Planning Agent — C1

    Analyses SkuHealth weeks-cover data to generate replenishment indent
    suggestions for Samsung:
    - Flags SKUs below REORDER_WEEKS_COVER threshold
    - Calculates qty needed to reach TARGET_WEEKS_COVER
    - Proposals are A1 — SCM Manager approves before submission to Samsung
    """

    agent_code = "C1"
    agent_name = "Indent Planning"

    def describe(self) -> str:
        return "Samsung replenishment indent generation from SkuHealth weeks-cover data."

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
        if action == "submit":
            if not payload.get("indent_ids") and not payload.get("submit_all"):
                return "'indent_ids' list or 'submit_all': true is required for submit"
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
        from app.models.enterprise import Indent, SkuHealth

        action = payload["action"]
        entity = payload.get("entity", "YEXIS-EL")
        today = date.today()

        if action == "plan":
            branch = payload.get("branch")
            q = db.query(SkuHealth)
            if branch:
                q = q.filter(SkuHealth.branch == branch)
            sku_rows = q.filter(
                SkuHealth.weeks_cover < REORDER_WEEKS_COVER,
                SkuHealth.eol_flag == False,  # noqa: E712
            ).order_by(SkuHealth.weeks_cover).all()

            suggestions = []
            created = 0
            for row in sku_rows:
                cover = row.weeks_cover or 0.0
                weekly_offtake = row.on_hand / cover if cover > 0 and row.on_hand else 0
                qty_needed = max(0, int((TARGET_WEEKS_COVER - cover) * weekly_offtake))
                if qty_needed <= 0:
                    continue

                # Idempotent: don't create duplicate draft indents for the same period
                existing = db.query(Indent).filter(
                    Indent.sku_ref == row.sku_ref,
                    Indent.entity == entity,
                    Indent.period == today,
                    Indent.status == "draft",
                ).first()
                if existing:
                    suggestions.append({
                        "indent_id": str(existing.id),
                        "sku_ref": row.sku_ref,
                        "branch": row.branch,
                        "weeks_cover_now": round(cover, 1),
                        "qty_suggested": existing.qty_suggested,
                        "action": "existing_draft",
                    })
                    continue

                indent = Indent(
                    sku_ref=row.sku_ref,
                    entity=entity,
                    period=today,
                    qty_suggested=qty_needed,
                    weeks_cover_now=round(cover, 1),
                    avg_weekly_offtake=round(weekly_offtake, 2),
                    status="draft",
                )
                db.add(indent)
                db.flush()
                suggestions.append({
                    "indent_id": str(indent.id),
                    "sku_ref": row.sku_ref,
                    "branch": row.branch,
                    "weeks_cover_now": round(cover, 1),
                    "avg_weekly_offtake": round(weekly_offtake, 2),
                    "qty_suggested": qty_needed,
                    "action": "created",
                })
                created += 1

            db.commit()
            return AgentResult(
                status="SUCCESS",
                output={
                    "entity": entity,
                    "period": str(today),
                    "skus_below_reorder_threshold": len(sku_rows),
                    "indent_suggestions_created": created,
                    "suggestions": suggestions,
                    "autonomy": "A1 — review and approve via action=submit before sending to Samsung",
                },
                actions_taken=[f"generated_{created}_indent_drafts"],
            )

        if action == "submit":
            now = datetime.now(timezone.utc)
            if payload.get("submit_all"):
                indents = db.query(Indent).filter(
                    Indent.entity == entity, Indent.status == "draft"
                ).all()
            else:
                indent_ids = payload["indent_ids"]
                indents = db.query(Indent).filter(Indent.id.in_(indent_ids)).all()

            if not indents:
                return AgentResult(
                    status="VALIDATION_ERROR",
                    error="No draft indents found to submit.",
                )

            for indent in indents:
                indent.status = "submitted"
                indent.submitted_ts = now

            db.commit()
            return AgentResult(
                status="NEEDS_APPROVAL",
                output={
                    "entity": entity,
                    "submitted_count": len(indents),
                    "indents": [
                        {"indent_id": str(i.id), "sku_ref": i.sku_ref, "qty": i.qty_suggested}
                        for i in indents
                    ],
                    "next_step": "SCM Manager to review and send to Samsung via official indent portal.",
                },
                actions_taken=[f"submitted_{len(indents)}_indents"],
            )

        if action == "report":
            indents = db.query(Indent).filter(Indent.entity == entity).order_by(Indent.period.desc()).limit(100).all()
            by_status: dict[str, list] = {}
            for i in indents:
                by_status.setdefault(i.status, []).append({
                    "indent_id": str(i.id),
                    "sku_ref": i.sku_ref,
                    "period": str(i.period),
                    "qty_suggested": i.qty_suggested,
                    "qty_approved": i.qty_approved,
                    "weeks_cover_now": i.weeks_cover_now,
                })
            return AgentResult(
                status="SUCCESS",
                output={"entity": entity, "by_status": {k: len(v) for k, v in by_status.items()}, "indents": by_status},
                actions_taken=["indent_report_generated"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
