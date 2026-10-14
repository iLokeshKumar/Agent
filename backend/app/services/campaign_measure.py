"""ISM Campaign Assistant — measurement (spec FR-MEA-01, 02, 04).

Attribution: every touch on an account before its deal was created gets a
share of the credit — 40% first touch, 40% last touch, 20% spread over the
middle (spec default; configurable per campaign in ism_brief_json
"attribution": [first, last, middle]). One touch = 100%; two = 50/50.

Forecast: with no closed-deal history yet, each open deal counts at its
stage probability (the deal's own Probability, else Zoho's default for the
stage). The range is an 80% band treating each deal as win/lose independently
(expected +- 1.28 sigma), labelled "uncalibrated" until 30 deals have closed.
"""

import math
from datetime import datetime, timezone

# Zoho CRM default stage probabilities (the stages present in this CRM).
STAGE_PROBABILITY = {
    "Qualification": 10, "Needs Analysis": 20, "Value Proposition": 40,
    "Identify Decision Makers": 60, "Perception Analysis": 70, "Proposal/Price Quote": 75,
    "Negotiation/Review": 90, "Closed Won": 100, "Closed Lost": 0, "Closed Lost to Competition": 0,
}
CALIBRATION_MIN_CLOSED = 30


def _aware(ts):
    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def touches_for_account(db, account_id: str, before: datetime | None = None) -> list[dict]:
    """All campaign touches on an account's contacts, oldest first."""
    from app.models.enterprise import CampaignContact, IsrTask, MessageLog
    refs = {account_id}
    for c in db.query(CampaignContact).all():
        if (c.extra_json or {}).get("account_id") == account_id:
            refs.add(c.contact_ref)
    out = []
    for m in db.query(MessageLog).filter(MessageLog.direction == "out", MessageLog.to_ref.in_(refs),
                                         MessageLog.correlation_type == "campaign",
                                         MessageLog.status.in_(("sent", "delivered", "read"))).all():
        out.append({"ts": _aware(m.ts), "campaign_id": m.correlation_ref, "kind": m.channel})
    for t in db.query(IsrTask).filter(IsrTask.contact_ref.in_(refs), IsrTask.status == "done",
                                      IsrTask.kind.in_(("call", "linkedin", "reply", "engagement"))).all():
        out.append({"ts": _aware(t.done_ts), "campaign_id": str(t.campaign_id) if t.campaign_id else None, "kind": t.kind})
    for c in db.query(CampaignContact).filter(CampaignContact.contact_ref.in_(refs), CampaignContact.response_ts.isnot(None)).all():
        out.append({"ts": _aware(c.response_ts), "campaign_id": str(c.campaign_id), "kind": "reply_received"})
    before = _aware(before)
    out = [t for t in out if t["ts"] is not None and t["campaign_id"] and (before is None or t["ts"] <= before)]
    return sorted(out, key=lambda t: t["ts"])


def split_credit(n: int, weights=(0.4, 0.4, 0.2)) -> list[float]:
    if n <= 0:
        return []
    if n == 1:
        return [1.0]
    if n == 2:
        return [0.5, 0.5]
    first, last, middle = weights
    mid = middle / (n - 2)
    return [first] + [mid] * (n - 2) + [last]


def attribute_deal(db, deal, weights=(0.4, 0.4, 0.2)) -> dict:
    touches = touches_for_account(db, deal.account_id, before=deal.created_ts) if deal.account_id else []
    credits = split_credit(len(touches), weights)
    by_campaign: dict[str, float] = {}
    for t, w in zip(touches, credits):
        by_campaign[t["campaign_id"]] = by_campaign.get(t["campaign_id"], 0.0) + w
    if deal.campaign_id and not touches:
        by_campaign[str(deal.campaign_id)] = 1.0  # logged against a campaign with no recorded touch
    deal.attribution_json = {
        **(deal.attribution_json or {}), "model": list(weights), "touches": len(touches),
        "by_campaign": {k: round(v, 4) for k, v in by_campaign.items()},
        "computed_ts": datetime.now(timezone.utc).isoformat(),
    }
    return deal.attribution_json


def campaign_attribution(db, campaign) -> dict:
    from app.models.enterprise import CampaignDeal
    weights = tuple((campaign.ism_brief_json or {}).get("attribution") or (0.4, 0.4, 0.2))
    cid = str(campaign.id)
    pipeline = won = 0.0
    deals = []
    for d in db.query(CampaignDeal).all():
        share = attribute_deal(db, d, weights)["by_campaign"].get(cid, 0.0)
        if share <= 0:
            continue
        credited = (d.amount or 0) * share
        if d.stage == "Closed Won":
            won += credited
        elif not d.stage.startswith("Closed"):
            pipeline += credited
        deals.append({"deal": d.name, "stage": d.stage, "amount": d.amount, "share": round(share, 3),
                      "credited": round(credited, 2), "account_id": d.account_id})
    db.commit()
    return {"campaign_id": cid, "model": list(weights), "pipeline_credited": round(pipeline, 2),
            "revenue_credited": round(won, 2), "deals": deals}


def forecast(db, campaign) -> dict:
    from app.models.enterprise import CampaignDeal
    cid = str(campaign.id)
    expected = var = 0.0
    rows = []
    closed = 0
    for d in db.query(CampaignDeal).all():
        if d.stage.startswith("Closed"):
            closed += 1
            continue
        share = attribute_deal(db, d)["by_campaign"].get(cid, 0.0)
        if share <= 0:
            continue
        p = (d.probability if d.probability is not None else STAGE_PROBABILITY.get(d.stage, 10)) / 100
        amt = (d.amount or 0) * share
        expected += amt * p
        var += (amt ** 2) * p * (1 - p)
        rows.append({"deal": d.name, "stage": d.stage, "amount_credited": round(amt, 2), "probability": round(p, 2)})
    sigma = math.sqrt(var)
    return {
        "campaign_id": cid, "expected": round(expected, 2),
        "low_80": round(max(0.0, expected - 1.28 * sigma), 2), "high_80": round(expected + 1.28 * sigma, 2),
        "open_deals": len(rows), "deals": rows,
        "calibrated": closed >= CALIBRATION_MIN_CLOSED,
        "note": None if closed >= CALIBRATION_MIN_CLOSED else
        f"Uncalibrated: {closed} closed deals so far; stage probabilities are Zoho defaults until {CALIBRATION_MIN_CLOSED} close.",
    }


def learning_record(db, campaign) -> dict:
    """FR-MEA-04: what converted, by segment, channel and A/B variant."""
    from app.models.enterprise import CampaignContact, CampaignDeal, IsrTask, MessageLog
    cid = str(campaign.id)
    contacts = db.query(CampaignContact).filter(CampaignContact.campaign_id == campaign.id).all()
    seg: dict[str, dict] = {}
    for c in contacts:
        s = seg.setdefault((c.extra_json or {}).get("segment", "unsegmented"),
                           {"contacts": 0, "sent": 0, "replied": 0, "positive": 0})
        s["contacts"] += 1
        s["sent"] += bool(c.message_sent_ts and c.message_log_id)
        s["replied"] += bool(c.response_ts)
        s["positive"] += c.response_type in ("interested", "callback", "converted")
    channels: dict[str, int] = {}
    for m in db.query(MessageLog).filter(MessageLog.correlation_ref == cid, MessageLog.direction == "out").all():
        channels[m.channel] = channels.get(m.channel, 0) + 1
    for t in db.query(IsrTask).filter(IsrTask.campaign_id == campaign.id, IsrTask.status == "done").all():
        channels[t.kind] = channels.get(t.kind, 0) + 1
    attr = campaign_attribution(db, campaign)
    ab = campaign.ab_test_json or {}
    return {
        "closed_ts": datetime.now(timezone.utc).isoformat(),
        "by_segment": seg, "touches_by_channel": channels,
        "ab_winner": ab.get("winner"), "ab_basis": ab.get("basis"),
        "pipeline_credited": attr["pipeline_credited"], "revenue_credited": attr["revenue_credited"],
        "deals": len(attr["deals"]),
    }
