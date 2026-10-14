"""K5 Response Tracker — inbound campaign response recording and lead classification.

Autonomy: A2 (supervised — response classification automatic from keyword
matching; lead escalation requires Sales Manager).

Actions:
  record   — record a response for a campaign contact
  classify — auto-classify unclassified responses by keyword
  report   — response summary for a campaign
"""

import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"record", "classify", "report"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL"}
VALID_RESPONSE_TYPES = {"interested", "not_interested", "callback", "converted", "no_response", "objection", "defer"}

# Keyword → response_type. Checked in this order, on whole words/phrases:
# negatives first, so "not interested" is never read as "interested"
# (the old substring map did exactly that, and "no" matched "know"/"now").
import re as _re

KEYWORD_MAP = {
    "not_interested": ["not interested", "no thanks", "no thank you", "unsubscribe",
                       "remove me", "take me off", "stop emailing", "stop sending",
                       "do not contact", "don't contact", "not required", "not needed"],
    "converted": ["purchase order", "po attached", "place the order", "confirm the order",
                  "order confirmed", "booked", "go ahead"],
    "callback": ["call me", "call back", "callback", "give me a call", "speak tomorrow",
                 "my number is", "phone"],
    # FR-EXE-03: "later" and "not convinced" are different follow-ups.
    "defer": ["not now", "next month", "next quarter", "next year", "next term", "next academic year",
              "after the exams", "after exams", "after budget", "after the budget", "after diwali",
              "after pongal", "busy now", "later this year", "contact me later", "get back later",
              "in a few months", "not at this time", "no budget this year"],
    "objection": ["too expensive", "costly", "price is high", "prices are high", "over budget",
                  "already have", "already using", "already bought", "already purchased",
                  "we have a vendor", "existing vendor", "existing supplier", "happy with our",
                  "not convinced", "why should", "cheaper elsewhere", "better price elsewhere"],
    "interested": ["interested", "tell me more", "send details", "share details",
                   "send the details", "send quote", "share quote", "quotation",
                   "pricing", "price", "demo", "yes"],
}
_KEYWORD_RES = {
    rt: _re.compile(r"\b(?:" + "|".join(_re.escape(k) for k in kws) + r")\b", _re.IGNORECASE)
    for rt, kws in KEYWORD_MAP.items()
}


_HANDOFF_RE = _re.compile(
    r"\b(quote|quotation|price list|pricing|proforma|site visit|visit our|survey|demo|demonstration|"
    r"specification|datasheet|data sheet|technical details)\b", _re.IGNORECASE)


def needs_handoff(text: str) -> bool:
    """FR-EXE-04: the reply asks for a quote, pricing, specs, a demo or a site
    visit -> Pre-Sales / Quote gets a task with the context pack."""
    return bool(_HANDOFF_RE.search(text or ""))


def _classify_keyword(text: str) -> str | None:
    for response_type, pattern in _KEYWORD_RES.items():
        if pattern.search(text or ""):
            return response_type
    return None


class K5ResponseTracker(BaseAgent):
    """Response Tracker — K5

    Processes inbound responses from campaign recipients:
    - Records response type and timestamp
    - Auto-classifies using keyword matching
    - Surfaces 'interested' and 'callback' leads for Sales team
    """

    agent_code = "K5"
    agent_name = "Response Tracker"

    def describe(self) -> str:
        return "Campaign response recording, classification, and lead surfacing."

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
        if action == "record":
            if not payload.get("contact_ref"):
                return "'contact_ref' is required for record"
            rt = payload.get("response_type")
            if rt and rt not in VALID_RESPONSE_TYPES:
                return f"response_type must be one of {sorted(VALID_RESPONSE_TYPES)}"
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
        campaign_id = payload["campaign_id"]
        campaign = db.get(Campaign, campaign_id)
        if campaign is None:
            return AgentResult(status="VALIDATION_ERROR", error=f"Campaign '{campaign_id}' not found")

        now = datetime.now(timezone.utc)

        if action == "record":
            contact_ref = payload["contact_ref"]
            contact = db.query(CampaignContact).filter(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.contact_ref == contact_ref,
            ).first()
            if contact is None:
                return AgentResult(status="VALIDATION_ERROR", error=f"Contact '{contact_ref}' not in campaign audience")

            response_type = payload.get("response_type")
            response_text = payload.get("response_text", "")

            # Auto-classify if not explicitly provided
            if not response_type and response_text:
                response_type = _classify_keyword(response_text)

            contact.response_ts = now
            contact.response_type = response_type
            if payload.get("notes"):
                contact.notes = payload["notes"]
            db.commit()

            crm_task_id = None
            if response_type in ("interested", "callback", "converted"):
                from app.services.campaign_intel import escalate_to_crm
                crm_task_id = escalate_to_crm(
                    db, contact, campaign, "reply",
                    f"Response recorded ({response_type}): {response_text or payload.get('notes') or '-'}",
                )
                if crm_task_id:
                    db.commit()

            lead_action = None
            if response_type in ("interested", "callback"):
                lead_action = f"Surface to Sales team: {contact_ref} responded '{response_type}' to campaign '{campaign.name}'"

            return AgentResult(
                status="SUCCESS",
                output={
                    "campaign_id": campaign_id,
                    "contact_ref": contact_ref,
                    "response_type": response_type,
                    "classified_from_text": bool(not payload.get("response_type") and response_type),
                    "lead_action": lead_action,
                    "crm_task_id": crm_task_id,
                },
                actions_taken=["response_recorded"],
            )

        if action == "classify":
            unclassified = db.query(CampaignContact).filter(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.response_ts != None,  # noqa: E711
                CampaignContact.response_type == None,  # noqa: E711
            ).all()

            classified_count = 0
            for contact in unclassified:
                text = contact.notes or ""
                rt = _classify_keyword(text)
                if rt:
                    contact.response_type = rt
                    classified_count += 1

            db.commit()
            return AgentResult(
                status="SUCCESS",
                output={"classified": classified_count, "still_unclassified": len(unclassified) - classified_count},
                actions_taken=[f"classified_{classified_count}_responses"],
            )

        if action == "report":
            contacts = db.query(CampaignContact).filter(CampaignContact.campaign_id == campaign.id).all()
            by_response: dict[str, int] = {}
            for c in contacts:
                rt = c.response_type or "no_response"
                by_response[rt] = by_response.get(rt, 0) + 1

            hot_leads = [
                {"contact_ref": c.contact_ref, "response_type": c.response_type, "notes": c.notes}
                for c in contacts if c.response_type in ("interested", "callback", "converted")
            ]
            return AgentResult(
                status="SUCCESS",
                output={
                    "campaign_id": campaign_id,
                    "campaign_name": campaign.name,
                    "total_audience": len(contacts),
                    "response_breakdown": by_response,
                    "hot_leads_count": len(hot_leads),
                    "hot_leads": hot_leads,
                    "response_rate_pct": round(
                        sum(1 for c in contacts if c.response_type) / len(contacts) * 100, 1
                    ) if contacts else 0,
                },
                actions_taken=["response_report_generated"],
            )

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")
