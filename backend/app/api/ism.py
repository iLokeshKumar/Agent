"""ISM Campaign Assistant API + console (spec sections 3-5).

All routes need a logged-in user with campaigns access, except the two
scheduler endpoints (/ism/nightly, /ism/isr-cards/send), which return
counts only — the same pattern as /mail/poll.
"""

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.db import get_db

router = APIRouter(prefix="/ism", tags=["ism"])

# CSV imports are only read from the project's campaign_phase0 folder.
IMPORT_DIR = (Path(__file__).resolve().parents[3] / "campaign_phase0").resolve()


def _user(request: Request):
    u = get_current_user(request)
    if u is None:
        raise HTTPException(401, "Log in first")
    if not u.can("campaigns"):
        raise HTTPException(403, "Campaigns access required")
    return u


def _import_path(name: str) -> str:
    p = (IMPORT_DIR / Path(name).name).resolve()
    if p.parent != IMPORT_DIR or not p.is_file():
        raise HTTPException(400, f"File must be an existing file in {IMPORT_DIR}")
    return str(p)


def _campaign(db, campaign_id: str):
    from app.models.enterprise import Campaign
    try:
        c = db.get(Campaign, uuid.UUID(campaign_id))
    except ValueError:
        raise HTTPException(400, "campaign_id must be a UUID")
    if c is None:
        raise HTTPException(404, "Campaign not found")
    return c


# ── scheduler endpoints ───────────────────────────────────────────────

@router.post("/nightly")
def nightly(db: Session = Depends(get_db)):
    from app.services.account_intel import committee_gap_tasks, score_all, sync_from_crm
    sync = sync_from_crm(db)
    tiers = score_all(db)
    gaps = committee_gap_tasks(db)
    return {"crm_sync": {k: v for k, v in sync.items() if k != "error"}, "crm_error": bool(sync.get("error")),
            "tiers": tiers, "research_tasks_created": gaps}


@router.post("/isr-cards/send")
def send_isr_cards(db: Session = Depends(get_db)):
    from app.services.isr_queue import isr_cards, send_internal_email
    cards = isr_cards(db)
    emailed = False
    if settings.ism_manager_email and cards:
        rows = "".join(
            f"<tr><td>{c['isr']}</td><td>{c['tasks_done']}/{c['tasks_due']}</td><td>{c['tasks_overdue_open']}</td>"
            f"<td>{c['calls_done']}</td><td>{c['replies']} ({c['positive_replies']} positive)</td>"
            f"<td>{c['deals_created']}</td><td>Rs {c['pipeline_created']:,.0f}</td>"
            f"<td>{c['drafts_sent_as_is']} / {c['drafts_edited']}</td></tr>" for c in cards)
        html = ("<p>ISR cards for the last 7 days.</p><table border='1' cellpadding='4' style='border-collapse:collapse'>"
                "<tr><th>ISR</th><th>Tasks done/due</th><th>Overdue open</th><th>Calls</th><th>Replies</th>"
                "<th>Deals</th><th>Pipeline</th><th>Drafts as-is / edited</th></tr>" + rows + "</table>")
        try:
            send_internal_email(settings.ism_manager_email, f"ISR cards, week to {date.today():%d %b %Y}", html)
            emailed = True
        except OSError:
            emailed = False
    return {"isrs": len(cards), "emailed": emailed}


# ── accounts ──────────────────────────────────────────────────────────

@router.post("/accounts/import")
def import_accounts(body: dict, request: Request, db: Session = Depends(get_db)):
    """Body: {"file": "pilot_candidates_education_government.csv", "pilot_only": false}"""
    _user(request)
    from app.services.account_intel import import_accounts_csv, score_all
    res = import_accounts_csv(db, _import_path(body.get("file", "pilot_candidates_education_government.csv")),
                              pilot_only=bool(body.get("pilot_only")))
    res["tiers"] = score_all(db)
    return res


@router.post("/orders/import")
def import_orders(body: dict, request: Request, db: Session = Depends(get_db)):
    """Tally sales-register CSV (party_name, gstin, date, value) in campaign_phase0."""
    _user(request)
    from app.services.account_intel import load_order_history_csv, score_all
    res = load_order_history_csv(db, _import_path(body["file"]))
    res["tiers"] = score_all(db)
    return res


@router.post("/accounts/score")
def rescore(request: Request, body: dict | None = None, db: Session = Depends(get_db)):
    _user(request)
    from app.services.account_intel import score_all
    return {"tiers": score_all(db, (body or {}).get("brief"))}


@router.get("/accounts")
def list_accounts(request: Request, tier: str | None = None, region: str | None = None,
                  vertical: str | None = None, owner: str | None = None, pilot: bool | None = None,
                  q: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    _user(request)
    from app.models.enterprise import AccountProfile
    qry = db.query(AccountProfile)
    for col, val in ((AccountProfile.tier, tier), (AccountProfile.region, region),
                     (AccountProfile.vertical, vertical), (AccountProfile.owner_isr, owner)):
        if val:
            qry = qry.filter(col == val)
    if pilot is not None:
        qry = qry.filter(AccountProfile.pilot.is_(pilot))
    if q:
        qry = qry.filter(AccountProfile.norm_name.like(f"%{q.upper()}%"))
    rows = qry.order_by(AccountProfile.fit_score.desc(), AccountProfile.intent_score.desc()).limit(max(1, min(limit, 1000))).all()
    return {"count": len(rows), "accounts": [{
        "account_id": p.account_id, "name": p.name, "region": p.region, "region_source": p.region_source,
        "vertical": p.vertical, "owner_isr": p.owner_isr, "pilot": p.pilot, "off_limits": p.off_limits,
        "tier": p.tier, "fit": p.fit_score, "intent": p.intent_score, "fit_reasons": p.fit_reasons_json,
        "phones": p.phones, "emails": p.emails, "email_on_dead_isp": p.email_on_dead_isp,
        "last_order": p.last_order_ts.date().isoformat() if p.last_order_ts else None,
        "open_deals": p.open_deal_count, "roles": list((p.roles_json or {}).keys()),
    } for p in rows]}


@router.patch("/accounts/{account_id}")
def edit_account(account_id: str, body: dict, request: Request, db: Session = Depends(get_db)):
    """ISM edits: region, vertical, owner_isr, pilot, off_limits, notes."""
    _user(request)
    from app.models.enterprise import AccountProfile
    from app.services.account_intel import fit_score, tier_for
    p = db.get(AccountProfile, account_id)
    if p is None:
        raise HTTPException(404, "Account not found")
    if "region" in body:
        p.region, p.region_source = body["region"] or None, "ism"
    if "vertical" in body:
        p.vertical, p.vertical_source = body["vertical"] or None, "ism"
    for k in ("owner_isr", "notes"):
        if k in body:
            setattr(p, k, body[k] or None)
    for k in ("pilot", "off_limits"):
        if k in body:
            setattr(p, k, bool(body[k]))
    p.fit_score, p.fit_reasons_json = fit_score(p)
    p.tier = tier_for(p.fit_score, p.intent_score)
    p.updated_ts = datetime.now(timezone.utc)
    db.commit()
    return {"account_id": account_id, "tier": p.tier, "fit": p.fit_score, "fit_reasons": p.fit_reasons_json}


# ── campaigns: brief -> audience -> review -> approve -> close ─────────

@router.get("/campaigns")
def list_campaigns(request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.models.enterprise import Campaign
    rows = db.query(Campaign).filter(Campaign.ism_managed.is_(True)).order_by(Campaign.created_ts.desc()).all()
    return [{"id": str(c.id), "ref": c.campaign_ref, "name": c.name, "status": c.status,
             "approved": bool(c.ism_approved_ts), "brief": c.ism_brief_json} for c in rows]


@router.post("/campaigns")
def create_campaign(body: dict, request: Request, db: Session = Depends(get_db)):
    """Body: {"name", "brief": {"verticals": [...], "regions": [...], "window_days": 60,
    "pipeline_target": 15000000, "isrs": [...], "category": "Interactive displays"},
    "template_id": "...", "default_channel": "email"}"""
    u = _user(request)
    from app.models.enterprise import Campaign
    if not body.get("name"):
        raise HTTPException(400, "name is required")
    c = Campaign(
        campaign_ref=f"ISM-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}",
        name=body["name"], campaign_type="end_customer", entity=body.get("entity", "YEXIS-EL"),
        status="draft", message_template_id=body.get("template_id"),
        default_channel=body.get("default_channel", "email"),
        ism_managed=True, ism_brief_json={**(body.get("brief") or {}), "created_by": u.username},
    )
    db.add(c)
    db.commit()
    return {"id": str(c.id), "ref": c.campaign_ref, "status": c.status}


@router.post("/campaigns/{campaign_id}/audience")
def build_audience(campaign_id: str, body: dict, request: Request, db: Session = Depends(get_db)):
    """Body: filters for the crm_accounts source — regions, verticals, tiers, owner_isr, pilot_only."""
    _user(request)
    c = _campaign(db, campaign_id)
    brief = c.ism_brief_json or {}
    from app.agents.k2_audience_builder import K2AudienceBuilder
    payload = {"action": "build", "campaign_id": campaign_id, "source": "crm_accounts",
               "regions": body.get("regions", brief.get("regions")),
               "verticals": body.get("verticals", brief.get("verticals")),
               "tiers": body.get("tiers"), "owner_isr": body.get("owner_isr"),
               "pilot_only": body.get("pilot_only", True),
               "waves": body.get("waves", brief.get("waves"))}
    agent = K2AudienceBuilder()
    err = agent.validate(payload)
    if err:
        raise HTTPException(400, err)
    r = agent.execute(payload, requested_by="ism")
    if r.status != "SUCCESS":
        raise HTTPException(422, r.error or "Audience build failed")
    return r.output


@router.get("/campaigns/{campaign_id}/review")
def review(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    """Everything the ISM checks before approving (spec 4.1 step 5)."""
    _user(request)
    from app.models.enterprise import CampaignContact, CampaignSequenceStep, TemplateRegistry
    from app.services.account_intel import capacity_check
    c = _campaign(db, campaign_id)
    contacts = db.query(CampaignContact).filter(CampaignContact.campaign_id == c.id).all()
    tiers: dict[str, int] = {}
    for x in contacts:
        t = (x.extra_json or {}).get("tier") or "-"
        tiers[t] = tiers.get(t, 0) + 1
    steps = db.query(CampaignSequenceStep).filter(CampaignSequenceStep.campaign_id == c.id) \
        .order_by(CampaignSequenceStep.step_number).all()
    tids = {s.template_id for s in steps} | ({c.message_template_id} if c.message_template_id else set())
    templates = {t.template_id: {"approval_status": t.approval_status, "purpose": t.purpose}
                 for t in db.query(TemplateRegistry).filter(TemplateRegistry.template_id.in_(tids)).all()}
    return {
        "campaign": {"id": str(c.id), "ref": c.campaign_ref, "name": c.name, "status": c.status,
                     "approved": c.ism_approved_ts.isoformat() if c.ism_approved_ts else None,
                     "approved_by": c.ism_approved_by, "brief": c.ism_brief_json},
        "contacts": len(contacts), "by_tier": tiers,
        "with_email": sum(1 for x in contacts if x.email), "with_phone": sum(1 for x in contacts if x.msisdn),
        "capacity": capacity_check(db, c),
        "first_template": c.message_template_id,
        "sequence": [{"step": s.step_number, "after_hours": s.delay_hours, "channel": s.channel,
                      "template_id": s.template_id, "condition": s.condition} for s in steps],
        "templates": templates,
        "missing_templates": sorted(t for t in tids if t not in templates),
        "unapproved_templates": sorted(k for k, v in templates.items() if v["approval_status"] == "pending"),
    }


@router.post("/campaigns/{campaign_id}/approve")
def approve(campaign_id: str, body: dict, request: Request, db: Session = Depends(get_db)):
    """Body: {"apply_trim": false}. Refuses while templates are missing or unapproved."""
    u = _user(request)
    from app.models.enterprise import CampaignContact
    from app.services.account_intel import capacity_check
    c = _campaign(db, campaign_id)
    r = review(campaign_id, request, db)
    if r["missing_templates"] or r["unapproved_templates"] or not c.message_template_id:
        raise HTTPException(409, {"error": "Fix templates before approval",
                                  "missing": r["missing_templates"], "unapproved": r["unapproved_templates"],
                                  "first_template_set": bool(c.message_template_id)})
    if r["contacts"] == 0:
        raise HTTPException(409, "No contacts in the audience")
    trimmed = 0
    if body.get("apply_trim"):
        refs = [t["contact_ref"] for t in capacity_check(db, c)["trim_suggested"]]
        if refs:
            trimmed = db.query(CampaignContact).filter(CampaignContact.campaign_id == c.id,
                                                       CampaignContact.contact_ref.in_(refs),
                                                       CampaignContact.message_sent_ts.is_(None)).delete(synchronize_session=False)
    c.ism_approved_ts = datetime.now(timezone.utc)
    c.ism_approved_by = u.username
    # Waves: approval releases wave 1 only; later waves via /waves/{n}/release.
    if db.query(CampaignContact).filter(CampaignContact.campaign_id == c.id).count() and any(
            (x.extra_json or {}).get("wave") for x in db.query(CampaignContact).filter(CampaignContact.campaign_id == c.id).all()):
        from sqlalchemy.orm.attributes import flag_modified
        brief = dict(c.ism_brief_json or {})
        brief.setdefault("waves_released", [1])
        brief.setdefault("waves_released_ts", {"1": c.ism_approved_ts.isoformat()})
        c.ism_brief_json = brief
        flag_modified(c, "ism_brief_json")
    db.commit()
    return {"approved": c.ism_approved_ts.isoformat(), "by": u.username, "trimmed": trimmed,
            "next": "Schedule the first send: POST /campaigns/{id}/schedule with template_id and scheduled_at."}


@router.post("/campaigns/{campaign_id}/close")
def close(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.services.campaign_measure import learning_record
    c = _campaign(db, campaign_id)
    rec = learning_record(db, c)
    c.kpi_json = {**(c.kpi_json or {}), "learning": rec}
    c.status, c.closed_ts = "closed", datetime.now(timezone.utc)
    db.commit()
    return rec


@router.get("/campaigns/{campaign_id}/attribution")
def attribution(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.services.campaign_measure import campaign_attribution
    return campaign_attribution(db, _campaign(db, campaign_id))


@router.get("/campaigns/{campaign_id}/forecast")
def forecast(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.services.campaign_measure import forecast as _f
    return _f(db, _campaign(db, campaign_id))


# ── ISR queue ─────────────────────────────────────────────────────────

def _task_json(t):
    return {"id": str(t.id), "kind": t.kind, "subject": t.subject, "body": t.body, "owner_isr": t.owner_isr,
            "due": t.due_date.isoformat(), "priority": t.priority, "status": t.status, "outcome": t.outcome,
            "contact_ref": t.contact_ref, "account_id": t.account_id,
            "campaign_id": str(t.campaign_id) if t.campaign_id else None, "variants": t.variants_json,
            "picked_variant": t.picked_variant, "crm_task_id": t.crm_task_id, "source": t.source}


@router.get("/queue")
def queue(request: Request, isr: str | None = None, status: str = "open", limit: int = 300,
          db: Session = Depends(get_db)):
    _user(request)
    from app.models.enterprise import IsrTask
    q = db.query(IsrTask)
    if status != "all":
        q = q.filter(IsrTask.status == status)
    if isr:
        q = q.filter(IsrTask.owner_isr == isr)
    order = {"Highest": 0, "High": 1, "Normal": 2, "Low": 3}
    rows = sorted(q.limit(max(1, min(limit, 2000))).all(), key=lambda t: (t.due_date, order.get(t.priority, 9)))
    return {"count": len(rows), "tasks": [_task_json(t) for t in rows]}


@router.post("/tasks/{task_id}/complete")
def complete(task_id: str, body: dict, request: Request, db: Session = Depends(get_db)):
    """Body: {"status": "done"|"skipped", "outcome": "...", "picked_variant": 0-2, "edited": bool}"""
    _user(request)
    from app.services.isr_queue import complete_task
    if body.get("status", "done") not in ("done", "skipped"):
        raise HTTPException(400, "status must be done or skipped")
    t = complete_task(db, uuid.UUID(task_id), status=body.get("status", "done"), outcome=body.get("outcome"),
                      picked_variant=body.get("picked_variant"), edited=body.get("edited"))
    if t is None:
        raise HTTPException(404, "Task not found")
    return _task_json(t)


@router.post("/tasks/{task_id}/drafts")
def drafts(task_id: str, request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.models.enterprise import IsrTask
    from app.services.isr_queue import draft_variants
    t = db.get(IsrTask, uuid.UUID(task_id))
    if t is None:
        raise HTTPException(404, "Task not found")
    try:
        return {"variants": draft_variants(db, t)}
    except Exception as exc:
        raise HTTPException(502, f"Draft generation failed: {exc}")


@router.get("/isr-cards")
def cards(request: Request, days: int = 7, db: Session = Depends(get_db)):
    _user(request)
    from app.services.isr_queue import isr_cards
    return {"days": days, "cards": isr_cards(db, days)}


# ── deals (local until Zoho CRM can create Deals) ─────────────────────

@router.post("/deals")
def log_deal(body: dict, request: Request, db: Session = Depends(get_db)):
    """Body: {"name", "account_id", "contact_ref"?, "campaign_id"?, "stage", "amount",
    "owner_isr"?, "expected_close"?: "YYYY-MM-DD"}"""
    _user(request)
    from app.models.enterprise import CampaignDeal
    from app.services.account_intel import refresh_deal_rollups
    from app.services.campaign_measure import STAGE_PROBABILITY
    for k in ("name", "account_id", "stage"):
        if not body.get(k):
            raise HTTPException(400, f"{k} is required")
    if body["stage"] not in STAGE_PROBABILITY:
        raise HTTPException(400, f"stage must be one of {list(STAGE_PROBABILITY)}")
    d = CampaignDeal(
        name=body["name"], account_id=body["account_id"], contact_ref=body.get("contact_ref"),
        campaign_id=uuid.UUID(body["campaign_id"]) if body.get("campaign_id") else None,
        stage=body["stage"], amount=float(body.get("amount") or 0), owner_isr=body.get("owner_isr"),
        probability=STAGE_PROBABILITY[body["stage"]],
        expected_close=date.fromisoformat(body["expected_close"]) if body.get("expected_close") else None,
    )
    db.add(d)
    db.commit()
    refresh_deal_rollups(db)
    return {"id": str(d.id), "stage": d.stage, "probability": d.probability}


@router.patch("/deals/{deal_id}")
def update_deal(deal_id: str, body: dict, request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.models.enterprise import CampaignDeal
    from app.services.account_intel import refresh_deal_rollups
    from app.services.campaign_measure import STAGE_PROBABILITY
    d = db.get(CampaignDeal, uuid.UUID(deal_id))
    if d is None:
        raise HTTPException(404, "Deal not found")
    if "stage" in body:
        if body["stage"] not in STAGE_PROBABILITY:
            raise HTTPException(400, f"stage must be one of {list(STAGE_PROBABILITY)}")
        d.stage, d.probability = body["stage"], STAGE_PROBABILITY[body["stage"]]
        if d.stage.startswith("Closed"):
            d.closed_ts = datetime.now(timezone.utc)
    if "amount" in body:
        d.amount = float(body["amount"] or 0)
    if "probability" in body:
        d.probability = int(body["probability"])
    db.commit()
    refresh_deal_rollups(db)
    return {"id": deal_id, "stage": d.stage, "amount": d.amount, "probability": d.probability}


# ── console page ──────────────────────────────────────────────────────

@router.get("/console", response_class=HTMLResponse, include_in_schema=False)
def console(request: Request):
    u = get_current_user(request)
    if u is None:
        return RedirectResponse("/login", status_code=302)
    if not u.can("campaigns"):
        raise HTTPException(403, "Campaigns access required")
    return HTMLResponse((Path(__file__).parent / "ism_console.html").read_text(encoding="utf-8"))


# ── Phase 2: pilot controls ───────────────────────────────────────────

@router.get("/campaigns/{campaign_id}/waves")
def waves(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.services.ism_pilot import wave_health
    c = _campaign(db, campaign_id)
    return {"released": (c.ism_brief_json or {}).get("waves_released"), "waves": wave_health(db, c),
            "log": (c.ism_brief_json or {}).get("wave_log")}


@router.post("/campaigns/{campaign_id}/waves/{wave}/release")
def release(campaign_id: str, wave: int, body: dict, request: Request, db: Session = Depends(get_db)):
    """Body: {"postmaster_checked": true, "force": false}. Refused while a released
    wave is over the bounce limit, too soon after the last wave, or unchecked."""
    u = _user(request)
    from app.services.ism_pilot import release_wave
    c = _campaign(db, campaign_id)
    if not c.ism_approved_ts:
        raise HTTPException(409, "Approve the campaign first")
    r = release_wave(db, c, wave, postmaster_checked=bool(body.get("postmaster_checked")),
                     by=u.username, force=bool(body.get("force")))
    if not r["ok"]:
        raise HTTPException(409, r)
    return r


@router.get("/campaigns/{campaign_id}/gate-report")
def gate_report(campaign_id: str, request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.services.ism_pilot import gate_report as _g
    return _g(db, _campaign(db, campaign_id))


@router.get("/templates/{template_id}")
def template_preview(template_id: str, request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if t is None:
        raise HTTPException(404, "Template not found")
    return {"template_id": t.template_id, "channel": t.channel, "subject": t.purpose, "body": t.body,
            "approval_status": t.approval_status, "approved_by": t.approved_by, "version": t.version}


@router.put("/templates/{template_id}")
def template_edit(template_id: str, body: dict, request: Request, db: Session = Depends(get_db)):
    """Body: {"subject"?, "body"?}. Any edit puts the template back to pending."""
    _user(request)
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if t is None:
        raise HTTPException(404, "Template not found")
    if "subject" in body:
        t.purpose = (body["subject"] or "")[:100]
    if "body" in body:
        if not (body["body"] or "").strip():
            raise HTTPException(400, "body cannot be empty")
        t.body = body["body"]
    t.approval_status, t.approved_by, t.version = "pending", None, (t.version or 1) + 1
    db.commit()
    return {"template_id": template_id, "approval_status": t.approval_status, "version": t.version}


@router.post("/templates/{template_id}/approve")
def template_approve(template_id: str, request: Request, db: Session = Depends(get_db)):
    """ISM sign-off on a template's exact wording (plan 6, step 5)."""
    u = _user(request)
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if t is None:
        raise HTTPException(404, "Template not found")
    if "[CONFIRM" in (t.body or "") or "[CONFIRM" in (t.purpose or ""):
        raise HTTPException(409, "Replace every [CONFIRM: ...] placeholder with a real fact before approving")
    t.approval_status, t.approved_by = "internal", u.username
    db.commit()
    return {"template_id": template_id, "approval_status": t.approval_status, "approved_by": u.username}


@router.get("/daily-brief")
def daily_brief_view(request: Request, db: Session = Depends(get_db)):
    _user(request)
    from app.services.ism_pilot import daily_brief
    return daily_brief(db)


@router.post("/daily-brief/send")
def daily_brief_send(db: Session = Depends(get_db)):
    """Scheduler: 08:30 IST. Emails ISM_MANAGER_EMAIL; returns counts only."""
    from app.services.ism_pilot import daily_brief
    from app.services.isr_queue import send_internal_email
    b = daily_brief(db)
    emailed = False
    if settings.ism_manager_email:
        from html import escape
        isr_rows = "".join(f"<tr><td>{escape(k)}</td><td>{v['due_today']}</td><td>{v['overdue']}</td>"
                           f"<td>{escape('; '.join(v['highest'][:5]))}</td></tr>" for k, v in sorted(b["isrs"].items()))
        reps = "".join(f"<li>{escape(r['company'] or '-')}: {escape(r['type'])}</li>" for r in b["replies_last_24h"]) or "<li>None</li>"
        hot = "".join(f"<li>{escape(h['company'] or '-')} (score {h['score']})</li>" for h in b["hot_not_replied"]) or "<li>None</li>"
        html = (f"<p>ISM brief for {b['date']}.</p><table border='1' cellpadding='4' style='border-collapse:collapse'>"
                "<tr><th>ISR</th><th>Due today</th><th>Overdue</th><th>Highest priority</th></tr>" + isr_rows +
                f"</table><p><b>Replies in the last 24 h</b></p><ul>{reps}</ul>"
                f"<p><b>Hot, not yet replied — call first</b></p><ul>{hot}</ul><p>Console: /ism/console</p>")
        try:
            send_internal_email(settings.ism_manager_email, f"ISM brief {b['date']}", html)
            emailed = True
        except OSError:
            emailed = False
    return {"isrs": len(b["isrs"]), "replies": len(b["replies_last_24h"]), "hot": len(b["hot_not_replied"]), "emailed": emailed}
