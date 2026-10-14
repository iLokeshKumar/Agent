"""F4 Interentity Reconciliation — nightly fingerprint matching.

Matches open transactions between YEXIS-EL and YEXIS-SL using
InterentityOpenItem rows. Auto-matches clear pairs; surfaces uncertain
pairs as NEEDS_APPROVAL proposals. All suggestions are A2 — no entry is
marked matched without human confirmation.

Payload:
  entity_from   "YEXIS-EL" or "YEXIS-SL"
  entity_to     counterpart entity
  as_of_date    YYYY-MM-DD (optional, defaults to today)
  action        "match" | "report" | "settle" (default "match")
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.agents.base import BaseAgent, AgentResult

VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}
VALID_ACTIONS = {"match", "report", "settle"}

# Amounts within this tolerance are considered equal
AMOUNT_TOLERANCE_PCT = Decimal("0.001")  # 0.1 %


class F4InterentityRecon(BaseAgent):
    """Interentity Reconciliation Agent — F4

    Matches intercompany transactions across YEXIS-EL and YEXIS-SL:
    - Fingerprints open items by doc_type + amount + approximate date
    - Auto-suggests matches for clear pairs
    - Surfaces uncertain / multi-match candidates for human review
    - Never marks a pair as matched without human sign-off (A2)
    """

    agent_code = "F4"
    agent_name = "Interentity Reconciliation"

    def describe(self) -> str:
        return (
            "Nightly fingerprint matching across YEXIS-EL and "
            "YEXIS-SL interentity open items."
        )

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"
        entity_from = payload.get("entity_from")
        entity_to = payload.get("entity_to")
        if not entity_from:
            return "'entity_from' is required"
        if not entity_to:
            return "'entity_to' is required"
        if entity_from not in VALID_ENTITIES:
            return f"entity_from must be one of {sorted(VALID_ENTITIES)}"
        if entity_to not in VALID_ENTITIES:
            return f"entity_to must be one of {sorted(VALID_ENTITIES)}"
        if entity_from == entity_to:
            return "entity_from and entity_to must be different"
        action = payload.get("action", "match")
        if action not in VALID_ACTIONS:
            return f"action must be one of {sorted(VALID_ACTIONS)}"
        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        from app.core.db import SessionLocal
        from app.models.enterprise import InterentityOpenItem

        entity_from = payload["entity_from"]
        entity_to = payload["entity_to"]
        action = payload.get("action", "match")
        as_of_str = payload.get("as_of_date")
        try:
            as_of = date.fromisoformat(as_of_str) if as_of_str else date.today()
        except ValueError:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"as_of_date '{as_of_str}' is not a valid YYYY-MM-DD date",
            )

        db = SessionLocal()
        try:
            return self._run(db, entity_from, entity_to, action, as_of)
        finally:
            db.close()

    def _run(self, db, entity_from: str, entity_to: str, action: str, as_of: date) -> AgentResult:
        from app.models.enterprise import InterentityOpenItem

        if action == "report":
            return self._report(db, entity_from, entity_to)

        # Load open items for both sides
        from_items = (
            db.query(InterentityOpenItem)
            .filter(
                InterentityOpenItem.entity_from == entity_from,
                InterentityOpenItem.entity_to == entity_to,
                InterentityOpenItem.match_status == "open",
            )
            .all()
        )
        to_items = (
            db.query(InterentityOpenItem)
            .filter(
                InterentityOpenItem.entity_from == entity_to,
                InterentityOpenItem.entity_to == entity_from,
                InterentityOpenItem.match_status == "open",
            )
            .all()
        )

        suggestions = []
        unmatched_from = []
        unmatched_to = list(to_items)

        for fi in from_items:
            candidates = []
            fi_amt = Decimal(str(fi.amount))
            for ti in unmatched_to:
                if ti.doc_type != fi.doc_type:
                    continue
                ti_amt = Decimal(str(ti.amount))
                # Amount match within tolerance
                if fi_amt == 0:
                    continue
                diff_pct = abs(fi_amt - ti_amt) / fi_amt
                if diff_pct <= AMOUNT_TOLERANCE_PCT:
                    # Date proximity: within 5 days
                    if fi.doc_ts and ti.doc_ts:
                        day_gap = abs((fi.doc_ts - ti.doc_ts).days)
                        if day_gap <= 5:
                            candidates.append((ti, diff_pct, day_gap))
                    else:
                        candidates.append((ti, diff_pct, 999))

            if len(candidates) == 1:
                ti, diff_pct, day_gap = candidates[0]
                # Single clear match — suggest auto
                suggestions.append({
                    "from_id": str(fi.id),
                    "to_id": str(ti.id),
                    "doc_type": fi.doc_type,
                    "amount": float(fi.amount),
                    "from_doc": fi.doc_no,
                    "to_doc": ti.doc_no,
                    "diff_pct": float(diff_pct),
                    "day_gap": day_gap,
                    "confidence": "high" if float(diff_pct) == 0 and day_gap <= 1 else "medium",
                })
                unmatched_to.remove(ti)
            elif len(candidates) > 1:
                suggestions.append({
                    "from_id": str(fi.id),
                    "doc_type": fi.doc_type,
                    "amount": float(fi.amount),
                    "candidate_count": len(candidates),
                    "confidence": "ambiguous",
                })
            else:
                unmatched_from.append({
                    "id": str(fi.id),
                    "doc_type": fi.doc_type,
                    "doc_no": fi.doc_no,
                    "amount": float(fi.amount),
                    "age_days": fi.age_days,
                })

        high_confidence = [s for s in suggestions if s.get("confidence") == "high"]
        medium_confidence = [s for s in suggestions if s.get("confidence") == "medium"]
        ambiguous = [s for s in suggestions if s.get("confidence") == "ambiguous"]
        still_unmatched_to = [
            {"id": str(ti.id), "doc_type": ti.doc_type, "doc_no": ti.doc_no,
             "amount": float(ti.amount)} for ti in unmatched_to
        ]

        status = "SUCCESS"
        actions_taken = [
            f"scanned_{len(from_items)}_from_items",
            f"scanned_{len(to_items)}_to_items",
            f"suggested_{len(suggestions)}_matches",
        ]

        if high_confidence:
            actions_taken.append(f"auto_suggest_{len(high_confidence)}_high_confidence")

        output = {
            "entity_from": entity_from,
            "entity_to": entity_to,
            "as_of_date": str(as_of),
            "summary": {
                "from_open": len(from_items),
                "to_open": len(to_items),
                "suggested": len(suggestions),
                "high_confidence": len(high_confidence),
                "medium_confidence": len(medium_confidence),
                "ambiguous": len(ambiguous),
                "unmatched_from": len(unmatched_from),
                "unmatched_to": len(still_unmatched_to),
            },
            "suggestions": suggestions,
            "unmatched_from": unmatched_from,
            "unmatched_to": still_unmatched_to,
            "next_step": (
                "Review high-confidence suggestions and approve via "
                "POST /approvals/{id}/approve to commit matches."
                if high_confidence else
                "No high-confidence matches found. Human review required for ambiguous items."
            ),
        }

        if suggestions and action == "match":
            # Store suggestions back to InterentityOpenItem.suggestion_json
            for s in suggestions:
                item = db.get(
                    __import__("app.models.enterprise", fromlist=["InterentityOpenItem"]).InterentityOpenItem,
                    s["from_id"]
                )
                if item:
                    item.match_status = "suggested"
                    item.suggestion_json = s
            db.commit()

        self.logger.info(
            "F4 recon %s↔%s: %d suggestions (%d high-conf), %d unmatched",
            entity_from, entity_to,
            len(suggestions), len(high_confidence),
            len(unmatched_from),
        )

        return AgentResult(
            status=status,
            output=output,
            actions_taken=actions_taken,
        )

    def _report(self, db, entity_from: str, entity_to: str) -> AgentResult:
        from app.models.enterprise import InterentityOpenItem

        rows = (
            db.query(InterentityOpenItem)
            .filter(
                InterentityOpenItem.entity_from == entity_from,
                InterentityOpenItem.entity_to == entity_to,
            )
            .all()
        )
        by_status: dict[str, list] = {}
        for r in rows:
            by_status.setdefault(r.match_status, []).append({
                "id": str(r.id),
                "doc_type": r.doc_type,
                "doc_no": r.doc_no,
                "amount": float(r.amount),
                "age_days": r.age_days,
            })

        return AgentResult(
            status="SUCCESS",
            output={
                "entity_from": entity_from,
                "entity_to": entity_to,
                "by_status": {k: len(v) for k, v in by_status.items()},
                "items": by_status,
            },
            actions_taken=["generated_recon_report"],
        )
