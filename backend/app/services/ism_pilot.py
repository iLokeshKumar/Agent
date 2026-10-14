"""ISM pilot controls (plan section 6 and 7): waves with a bounce gate,
the G1/G2 gate report, and the 08:30 IST daily ISM brief.

Gate thresholds are the plan's proposed pilot gates; the ISM can change
them per campaign in ism_brief_json["gates"].
"""

import statistics
from datetime import datetime, timedelta, timezone

DEFAULT_GATES = {
    "max_hard_bounce_pct": 2.0,      # per wave
    "min_positive_reply_pct": 2.5,   # spec baseline (May 2026)
    "min_meeting_pct": 1.2,          # spec baseline: sequence-to-meeting
    "max_first_touch_hours_p1": 24,  # spec 6-month target, used as the pilot gate
    "min_days_between_waves": 5,
}
MEETING_STAGES = ("Needs Analysis", "Value Proposition", "Identify Decision Makers", "Perception Analysis",
                  "Proposal/Price Quote", "Negotiation/Review", "Closed Won")


def _aware(ts):
    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def gates(campaign) -> dict:
    return {**DEFAULT_GATES, **((campaign.ism_brief_json or {}).get("gates") or {})}


def wave_health(db, campaign) -> list[dict]:
    from app.models.enterprise import CampaignContact, ContactOptOut, EmailBounceEvent
    contacts = db.query(CampaignContact).filter(CampaignContact.campaign_id == campaign.id).all()
    hard = {b.email.strip().lower() for b in db.query(EmailBounceEvent).filter(EmailBounceEvent.bounce_class == "hard").all()}
    unsub = {o.contact_ref for o in db.query(ContactOptOut).filter(ContactOptOut.campaign_id == str(campaign.id)).all()}
    released = (campaign.ism_brief_json or {}).get("waves_released") or []
    rel_ts = (campaign.ism_brief_json or {}).get("waves_released_ts") or {}
    waves: dict[int, dict] = {}
    for c in contacts:
        w = (c.extra_json or {}).get("wave") or 1
        x = waves.setdefault(w, {"wave": w, "contacts": 0, "emailed": 0, "first_touch_calls": 0,
                                 "hard_bounces": 0, "unsubscribed": 0, "replied": 0})
        x["contacts"] += 1
        if c.message_log_id:
            x["emailed"] += 1
            if c.email and c.email.strip().lower() in hard:
                x["hard_bounces"] += 1
        elif c.message_sent_ts:
            x["first_touch_calls"] += 1
        x["unsubscribed"] += c.contact_ref in unsub
        x["replied"] += bool(c.response_ts)
    g = gates(campaign)
    for x in waves.values():
        x["released"] = x["wave"] in released
        x["released_ts"] = rel_ts.get(str(x["wave"]))
        x["hard_bounce_pct"] = round(100 * x["hard_bounces"] / x["emailed"], 2) if x["emailed"] else None
        x["healthy"] = x["hard_bounce_pct"] is None or x["hard_bounce_pct"] < g["max_hard_bounce_pct"]
    return [waves[k] for k in sorted(waves)]


def release_wave(db, campaign, wave: int, *, postmaster_checked: bool, by: str, force: bool = False) -> dict:
    """Release the next wave only if every released wave is healthy, enough
    days have passed, and someone confirmed Google Postmaster shows no spam
    complaints (complaints are not visible to the platform itself)."""
    from sqlalchemy.orm.attributes import flag_modified
    brief = dict(campaign.ism_brief_json or {})
    released = list(brief.get("waves_released") or [])
    rel_ts = dict(brief.get("waves_released_ts") or {})
    health = {h["wave"]: h for h in wave_health(db, campaign)}
    if wave not in health:
        return {"ok": False, "reason": f"wave {wave} has no contacts"}
    if wave in released:
        return {"ok": True, "reason": "already released", "waves_released": released}
    problems = []
    if released and wave != max(released) + 1:
        problems.append(f"release waves in order; next is {max(released) + 1}")
    for w in released:
        if not health.get(w, {}).get("healthy", True):
            problems.append(f"wave {w} hard bounces {health[w]['hard_bounce_pct']}% (limit {gates(campaign)['max_hard_bounce_pct']}%)")
    if released:
        last = max(datetime.fromisoformat(t) for t in rel_ts.values()) if rel_ts else None
        min_days = gates(campaign)["min_days_between_waves"]
        if last and datetime.now(timezone.utc) - last < timedelta(days=min_days):
            problems.append(f"previous wave released {last:%d %b}; wait {min_days} days between waves")
        if not postmaster_checked:
            problems.append("confirm Google Postmaster Tools shows no spam complaints for the sending domain")
    if problems and not force:
        return {"ok": False, "reason": "; ".join(problems), "health": list(health.values())}
    released.append(wave)
    rel_ts[str(wave)] = datetime.now(timezone.utc).isoformat()
    brief.update(waves_released=sorted(released), waves_released_ts=rel_ts)
    log = list(brief.get("wave_log") or [])
    log.append({"wave": wave, "by": by, "ts": rel_ts[str(wave)], "forced": bool(problems and force),
                "override_reason": "; ".join(problems) if problems else None})
    brief["wave_log"] = log
    campaign.ism_brief_json = brief
    flag_modified(campaign, "ism_brief_json")
    db.commit()
    return {"ok": True, "waves_released": brief["waves_released"], "forced": bool(problems and force)}


def gate_report(db, campaign) -> dict:
    """The plan's section 7 table, computed from live data."""
    from app.models.enterprise import CampaignContact, CampaignDeal, IsrTask
    from app.services.campaign_measure import campaign_attribution
    g = gates(campaign)
    contacts = db.query(CampaignContact).filter(CampaignContact.campaign_id == campaign.id).all()
    enrolled = [c for c in contacts if c.message_sent_ts]
    emailed = [c for c in enrolled if c.message_log_id]
    positive = [c for c in contacts if c.response_type in ("interested", "callback", "converted")]
    attr = campaign_attribution(db, campaign)
    deal_names = {d["deal"] for d in attr["deals"]}
    meetings = [d for d in db.query(CampaignDeal).all()
                if (d.name in deal_names or d.campaign_id == campaign.id) and d.stage in MEETING_STAGES]
    # signal -> first touch for P1: reply/engagement task created -> done
    p1_refs = {c.contact_ref for c in contacts if (c.extra_json or {}).get("tier") == "P1"}
    hours = []
    for t in db.query(IsrTask).filter(IsrTask.campaign_id == campaign.id, IsrTask.kind.in_(("reply", "engagement")),
                                      IsrTask.status == "done").all():
        if t.contact_ref in p1_refs and t.done_ts:
            hours.append((_aware(t.done_ts) - _aware(t.created_ts)).total_seconds() / 3600)
    per_isr: dict[str, int] = {}
    for c in contacts:
        k = (c.extra_json or {}).get("owner_isr") or "(unassigned)"
        per_isr[k] = per_isr.get(k, 0) + 1
    # daily queue use: share of working days in the last 14 on which each ISR completed >= 1 task
    since = datetime.now(timezone.utc) - timedelta(days=14)
    days_used: dict[str, set] = {}
    for t in db.query(IsrTask).filter(IsrTask.status == "done").all():
        if t.done_ts and _aware(t.done_ts) >= since:
            days_used.setdefault(t.owner_isr or "(unassigned)", set()).add(_aware(t.done_ts).date())
    waves = wave_health(db, campaign)
    pct = lambda a, b: round(100 * a / b, 2) if b else None
    pos_pct, meet_pct = pct(len(positive), len(emailed)), pct(len(meetings), len(enrolled))
    med = round(statistics.median(hours), 1) if hours else None
    checks = [
        {"measure": "Hard bounce rate, every released wave", "value": [w["hard_bounce_pct"] for w in waves if w["released"]],
         "gate": f"< {g['max_hard_bounce_pct']}%", "pass": all(w["healthy"] for w in waves if w["released"])},
        {"measure": "Positive email reply rate", "value": pos_pct, "gate": f">= {g['min_positive_reply_pct']}%",
         "pass": pos_pct is not None and pos_pct >= g["min_positive_reply_pct"]},
        {"measure": "Sequence-to-meeting conversion", "value": meet_pct, "gate": f">= {g['min_meeting_pct']}%",
         "pass": meet_pct is not None and meet_pct >= g["min_meeting_pct"]},
        {"measure": "Signal to first touch, P1, median hours", "value": med,
         "gate": f"< {g['max_first_touch_hours_p1']} h", "pass": med is not None and med < g["max_first_touch_hours_p1"]},
        {"measure": "Spam complaint rate", "value": "read in Google Postmaster Tools", "gate": "< 0.1%", "pass": None},
    ]
    return {
        "campaign": campaign.name, "computed_ts": datetime.now(timezone.utc).isoformat(),
        "enrolled": len(enrolled), "emailed": len(emailed), "positive_replies": len(positive),
        "meetings": len(meetings), "p1_first_touch_samples": len(hours),
        "accounts_per_isr": per_isr, "isr_active_days_last_14": {k: len(v) for k, v in days_used.items()},
        "pipeline_credited": attr["pipeline_credited"], "revenue_credited": attr["revenue_credited"],
        "waves": waves, "checks": checks,
        "gate_passed": all(c["pass"] for c in checks if c["pass"] is not None),
    }


def daily_brief(db) -> dict:
    """08:30 IST: per ISR open tasks due today / overdue, yesterday's replies,
    hot contacts not yet called — for every live ISM campaign."""
    from app.models.enterprise import Campaign, CampaignContact, IsrTask
    from app.services.campaign_intel import contact_scores
    from app.services.isr_queue import today_ist
    today = today_ist()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    isrs: dict[str, dict] = {}
    for t in db.query(IsrTask).filter(IsrTask.status == "open").all():
        x = isrs.setdefault(t.owner_isr or "(unassigned)", {"due_today": 0, "overdue": 0, "highest": []})
        if t.due_date == today:
            x["due_today"] += 1
        elif t.due_date < today:
            x["overdue"] += 1
        if t.priority == "Highest" and t.due_date <= today:
            x["highest"].append(t.subject)
    replies, hot = [], []
    for camp in db.query(Campaign).filter(Campaign.ism_managed.is_(True), Campaign.status == "broadcast").all():
        for c in db.query(CampaignContact).filter(CampaignContact.campaign_id == camp.id,
                                                  CampaignContact.response_ts.isnot(None)).all():
            if _aware(c.response_ts) >= since:
                replies.append({"campaign": camp.name, "company": c.company, "type": c.response_type or "unclassified"})
        for r in contact_scores(db, camp).values():
            if r["tier"] == "hot" and not r["replied"]:
                hot.append({"campaign": camp.name, "company": r["company"], "score": r["score"]})
    return {"date": today.isoformat(), "isrs": isrs, "replies_last_24h": replies,
            "hot_not_replied": sorted(hot, key=lambda h: -h["score"])[:20]}
