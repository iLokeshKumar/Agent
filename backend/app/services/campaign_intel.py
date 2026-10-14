"""Campaign intelligence for K3/K5/K6 — makes campaigns act on their own data.

  1. Engagement scoring — per contact, from opens, clicks and replies.
       open points: 1 open = 1, 2 opens = 3, 3+ opens = 5
       click: 5 each (max 10)      reply: 10      positive reply: +5
     Tiers: hot >= K3_HOT_SCORE_THRESHOLD (default 5), warm >= 2, else cold;
     a "not interested" reply is its own tier and never goes on a call list.
     Opens are noisy (Apple Mail pre-fetches, Outlook blocks images), so
     clicks and replies carry most of the weight.

  2. Hot leads -> Zoho CRM task — a positive reply, or a contact turning
     hot from engagement, becomes a Task for a person to act on today. The
     prospect is matched to an existing CRM Contact/Lead by email, or
     created as a Lead. Once per contact per reason (idempotent).

  3. Cold stop — after K3_COLD_STOP_AFTER_EMAILS emails with no open, no
     click and no reply, stop emailing that contact. Repeatedly mailing
     people who never engage is what drags inbox placement down for
     everyone else on the list.

  4. A/B winner — test a share of the audience, then pick the variant with
     the best reply rate (ties: click rate, then open rate) and send only
     the winner to the rest.

Everything here is best-effort: a CRM outage never blocks a send or a
mailbox poll — failures are logged and retried on the next tick.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings

logger = logging.getLogger(__name__)

HOT_RESPONSES = ("interested", "callback", "converted")

# Set to True the first time Zoho returns MAX_LIMIT_REACHED so we stop
# hammering the API and spamming logs on every scheduler tick.
_crm_at_capacity: bool = False


# ── 1. Engagement scoring ─────────────────────────────────────────────

def _open_points(opens: int) -> int:
    return 0 if opens <= 0 else 1 if opens == 1 else 3 if opens == 2 else 5


def contact_scores(db, campaign) -> dict[str, dict]:
    """{contact_ref: {opens, clicks, emails_sent, replied, response_type, score, tier}}"""
    from app.models.enterprise import CampaignContact, EmailEngagementEvent, MessageLog

    cid = campaign.id
    ev = dict()
    for ref, etype, n in (
        db.query(EmailEngagementEvent.contact_ref, EmailEngagementEvent.event_type, func.count())
        .filter(EmailEngagementEvent.campaign_id == cid)
        .group_by(EmailEngagementEvent.contact_ref, EmailEngagementEvent.event_type)
        .all()
    ):
        ev.setdefault(ref, {"open": 0, "click": 0})[etype] = n

    sends = dict(
        db.query(MessageLog.to_ref, func.count())
        .filter(MessageLog.correlation_ref == str(cid), MessageLog.channel == "email",
                MessageLog.status == "sent")
        .group_by(MessageLog.to_ref)
        .all()
    )

    threshold = settings.k3_hot_score_threshold
    out: dict[str, dict] = {}
    for c in db.query(CampaignContact).filter(CampaignContact.campaign_id == cid).all():
        e = ev.get(c.contact_ref, {"open": 0, "click": 0})
        opens, clicks = e.get("open", 0), e.get("click", 0)
        replied = c.response_ts is not None
        score = _open_points(opens) + min(clicks, 2) * 5
        if replied:
            score += 10
            if c.response_type in HOT_RESPONSES:
                score += 5
        if c.response_type == "not_interested":
            score, tier = 0, "not_interested"   # said no — never on the call list
        else:
            tier = "hot" if score >= threshold else "warm" if score >= 2 else "cold"
        out[c.contact_ref] = {
            "contact_ref": c.contact_ref, "name": c.name, "company": c.company, "email": c.email,
            "opens": opens, "clicks": clicks, "emails_sent": sends.get(c.contact_ref, 0),
            "replied": replied, "response_type": c.response_type,
            "ab_variant": c.ab_variant, "score": score, "tier": tier,
        }
    return out


def is_cold_stop(score_row: dict | None) -> bool:
    """True if this contact has had enough emails with zero engagement."""
    limit = settings.k3_cold_stop_after_emails
    if not limit or not score_row:
        return False
    return (score_row["emails_sent"] >= limit and score_row["opens"] == 0
            and score_row["clicks"] == 0 and not score_row["replied"])


# ── 2. Hot leads -> Zoho CRM task ─────────────────────────────────────

def _marker(contact) -> dict:
    extra = dict(contact.extra_json or {})
    return dict(extra.get("_k3") or {})


def _set_marker(contact, key: str, value) -> None:
    extra = dict(contact.extra_json or {})
    k3 = dict(extra.get("_k3") or {})
    k3[key] = value
    extra["_k3"] = k3
    contact.extra_json = extra
    flag_modified(contact, "extra_json")


def escalate_to_crm(db, contact, campaign, reason: str, detail: str) -> str | None:
    """Put a hot reply or hot engagement in front of a person today.

    Always creates a task in the platform's ISR queue (app/services/isr_queue)
    and mirrors it to Zoho CRM when the CRM accepts new records. reason is
    'reply' or 'engagement'; each is raised once per contact. Returns the
    local task id (as a string), or None if skipped.
    """
    if not settings.k3_crm_escalation_enabled:
        return None
    markers = _marker(contact)
    if markers.get(f"crm_task_{reason}") or (reason == "engagement" and markers.get("crm_task_reply")):
        return None
    from app.services.isr_queue import create_task, today_ist

    who = contact.name or contact.company or contact.email or contact.contact_ref
    if reason == "reply":
        label = {"interested": "Interested", "callback": "Wants a call", "converted": "Ready to order",
                 "objection": "Objection", "defer": "Asked to follow up later"}.get(contact.response_type, "Replied")
        subject = f"{label}: {who} replied to '{campaign.name}'"
        priority = "Highest" if contact.response_type in ("callback", "converted") else "High"
    else:
        subject = f"Call today: {who} is engaging with '{campaign.name}'"
        priority = "High"
    body = (f"{detail}\n\nCompany: {contact.company or '-'}\nEmail: {contact.email or '-'}\n"
            f"Phone: {contact.msisdn or '-'}\nCampaign: {campaign.name}\n"
            "Raised automatically by the campaign agent. Use 'Draft replies' in the ISM console for 3 suggested responses.")
    account_id = (contact.extra_json or {}).get("account_id")
    from datetime import timedelta
    due = today_ist() + timedelta(days=30) if contact.response_type == "defer" else today_ist()
    task, _ = create_task(
        db, kind=reason, subject=subject, body=body, contact_ref=contact.contact_ref,
        account_id=account_id, campaign_id=getattr(campaign, "id", None), due=due,
        priority=priority, source=reason, push_crm=True, email=contact.email,
    )
    _set_marker(contact, f"crm_task_{reason}", task.crm_task_id or f"local:{task.id}")
    contact.notes = ((contact.notes + "\n") if contact.notes else "") + \
        f"[K3] Task {task.id} for {task.owner_isr or 'unassigned'} ({reason})" + \
        (f", Zoho CRM task {task.crm_task_id}." if task.crm_task_id else ", local queue only (CRM did not accept it).")
    return str(task.id)


def escalate_hot_engagement(db, campaign, limit: int = 20) -> int:
    """Put contacts who turned hot from opens/clicks (but haven't replied)
    on the CRM call list. Called from the K3 scheduler tick."""
    from app.models.enterprise import CampaignContact
    if not settings.k3_crm_escalation_enabled:
        return 0
    scores = contact_scores(db, campaign)
    hot = [r for r in scores.values() if r["tier"] == "hot" and not r["replied"]]
    if not hot:
        return 0
    by_ref = {
        c.contact_ref: c for c in db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.contact_ref.in_([r["contact_ref"] for r in hot]),
        ).all()
    }
    created = 0
    for r in sorted(hot, key=lambda x: -x["score"]):
        if created >= limit:
            break
        c = by_ref.get(r["contact_ref"])
        if c is None or _marker(c).get("crm_task_engagement") or _marker(c).get("crm_task_reply"):
            continue
        detail = (f"Opened {r['opens']} time(s), clicked {r['clicks']} link(s) "
                  f"across {r['emails_sent']} email(s). Engagement score {r['score']}.")
        if escalate_to_crm(db, c, campaign, "engagement", detail):
            created += 1
    if created:
        db.commit()
    return created


# ── 4. A/B winner ─────────────────────────────────────────────────────

def variant_stats(db, campaign) -> dict[str, dict]:
    from app.models.enterprise import CampaignContact, EmailEngagementEvent
    ab = campaign.ab_test_json or {}
    names = [v.get("name") for v in ab.get("variants", []) if v.get("name")]
    stats = {n: {"assigned": 0, "sent": 0, "replied": 0, "positive": 0, "opened": 0,
                 "clicked": 0, "last_sent": None} for n in names}
    contacts = db.query(CampaignContact).filter(
        CampaignContact.campaign_id == campaign.id, CampaignContact.ab_variant.isnot(None),
    ).all()
    engaged: dict[str, set] = {"open": set(), "click": set()}
    for ref, etype in (
        db.query(EmailEngagementEvent.contact_ref, EmailEngagementEvent.event_type)
        .filter(EmailEngagementEvent.campaign_id == campaign.id).distinct().all()
    ):
        engaged.setdefault(etype, set()).add(ref)
    for c in contacts:
        s = stats.get(c.ab_variant)
        if s is None:
            continue
        s["assigned"] += 1
        # Actually delivered to the provider (bounce/opt-out skips set
        # message_sent_ts but never message_log_id).
        if c.message_sent_ts and c.message_log_id:
            s["sent"] += 1
            if s["last_sent"] is None or c.message_sent_ts > s["last_sent"]:
                s["last_sent"] = c.message_sent_ts
            s["replied"] += bool(c.response_ts)
            s["positive"] += c.response_type in HOT_RESPONSES
            s["opened"] += c.contact_ref in engaged["open"]
            s["clicked"] += c.contact_ref in engaged["click"]
    for s in stats.values():
        sent = s["sent"] or 1
        s["reply_rate_pct"] = round(s["replied"] / sent * 100, 1)
        s["click_rate_pct"] = round(s["clicked"] / sent * 100, 1)
        s["open_rate_pct"] = round(s["opened"] / sent * 100, 1)
        s["last_sent"] = s["last_sent"].isoformat() if s["last_sent"] else None
    return stats


def ab_test_size(campaign, total_audience: int) -> int:
    ab = campaign.ab_test_json or {}
    n_variants = max(1, len(ab.get("variants", [])))
    if ab.get("test_size"):
        return int(ab["test_size"])
    frac = float(ab.get("test_fraction", settings.k3_ab_test_fraction))
    return max(settings.k3_ab_min_per_variant * n_variants, math.ceil(total_audience * frac))


def decide_ab_winner(db, campaign) -> str | None:
    """Pick and persist a winner once the test has enough sends and has
    had time to collect replies. Returns the winner (existing or new),
    or None while the test is still running. A manually set
    ab_test_json['winner'] is always respected."""
    ab = dict(campaign.ab_test_json or {})
    if ab.get("winner"):
        return ab["winner"]
    stats = variant_stats(db, campaign)
    if len(stats) < 2:
        return None

    min_n = int(ab.get("min_per_variant", settings.k3_ab_min_per_variant))
    wait_h = float(ab.get("decide_after_hours", settings.k3_ab_decide_after_hours))
    # Unreachable test contacts never get sent, so don't wait for "all
    # assigned"; the quiet period after the last test send covers stragglers.
    if any(s["sent"] < min_n for s in stats.values()):
        return None
    last = max(datetime.fromisoformat(s["last_sent"]) for s in stats.values() if s["last_sent"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - last < timedelta(hours=wait_h):
        return None

    ranked = sorted(stats.items(), key=lambda kv: (
        kv[1]["reply_rate_pct"], kv[1]["click_rate_pct"], kv[1]["open_rate_pct"]), reverse=True)
    best_name, best = ranked[0]
    basis = ("reply_rate" if best["replied"] else "click_rate" if best["clicked"]
             else "open_rate" if best["opened"] else "no_signal")
    ab.update({
        "winner": best_name, "basis": basis,
        "decided_at": datetime.now(timezone.utc).isoformat(), "test_stats": stats,
    })
    campaign.ab_test_json = ab
    flag_modified(campaign, "ab_test_json")
    db.commit()
    logger.info("A/B winner for %s: %s (%s)", campaign.campaign_ref, best_name, basis)
    return best_name
