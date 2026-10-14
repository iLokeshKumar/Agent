"""ISM Campaign Assistant — account intelligence, segmentation and capacity
(spec FR-ACC-01..04, FR-SEG-01..03).

Data sources, in order of trust:
  * the ISM's curated pilot sheet (campaign_phase0/pilot_candidates_*.csv):
    region_confirmed, owner_isr, pilot_yes_no are human-confirmed;
  * account names (vertical/place keywords) — marked source='name';
  * Zoho CRM people-contacts (title -> buying role) and Deals, when present;
  * a Tally sales-register export for order history (load_order_history_csv);
  * the platform's own engagement data (opens, clicks, replies) for intent.

Every score carries its reasons so the ISM can see why an account ranks
where it does. Nothing here invents data: a missing input scores zero and
is named in the reasons.
"""

import csv
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── normalisation and name rules (same rules as the Phase 0 lists) ─────

def norm(s: str | None) -> str:
    s = (s or "").upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\b(M S|MS|THE|PVT|PRIVATE|LTD|LIMITED|LLP|CO|COMPANY|INC|CORP|CORPORATION)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_EDU = r"\b(SCHOOL|COLLEGE|UNIVERSITY|INSTITUTE|INSTITUTION|POLYTECHNIC|VIDYALAYA|VIDYALAYAM|MATRIC|ACADEMY|ITI)\b"
_GOV = r"\b(GOVT|GOVERNMENT|DEPARTMENT|DIRECTORATE|COMMISSIONER|MUNICIPAL|MUNICIPALITY|PANCHAYAT|COLLECTORATE|DISTRICT)\b"
_PSU = r"\b(BHEL|BSNL|ONGC|NLC|IOCL|INDIAN OIL|HPCL|BPCL|SAIL|GAIL|NTPC|AIRPORTS AUTHORITY|PORT TRUST|RAILWAY|TANGEDCO|TNEB|KSEB|SIPCOT|TIDCO|ELCOT|KELTRON)\b"
_HEALTH = r"\b(HOSPITAL|HOSPITALS|CLINIC|MEDICAL|HEALTHCARE|DIAGNOSTIC|DIAGNOSTICS)\b"
_TN = r"\b(CHENNAI|MADRAS|COIMBATORE|MADURAI|TRICHY|TIRUCHIRAPPALLI|SALEM|TIRUPPUR|TIRUNELVELI|ERODE|VELLORE|THANJAVUR|HOSUR|KANCHIPURAM|KANCHEEPURAM|TUTICORIN|THOOTHUKUDI|NAGERCOIL|DINDIGUL|KARUR|NAMAKKAL|CUDDALORE|PONDICHERRY|PUDUCHERRY|TAMIL NADU|TAMILNADU|SRIPERUMBUDUR|AMBATTUR|GUINDY|TAMBARAM)\b"
_KL = r"\b(KOCHI|COCHIN|ERNAKULAM|TRIVANDRUM|THIRUVANANTHAPURAM|KOZHIKODE|CALICUT|THRISSUR|TRICHUR|KOLLAM|KANNUR|PALAKKAD|KOTTAYAM|ALAPPUZHA|KERALA|MALAPPURAM)\b"
_DEAD_ISP = ("vsnl.com", "vsnl.net", "vsnl.in", "eth.net", "satyam.net.in", "sify.com")


def vertical_from_name(name: str) -> str | None:
    n = norm(name)
    if re.search(_EDU, n):
        return "Education"
    for v, rx in (("PSU", _PSU), ("Government", _GOV), ("Healthcare", _HEALTH)):
        if re.search(rx, n):
            return v
    return None


def region_from_name(name: str) -> str | None:
    n = norm(name)
    if re.search(_TN, n):
        return "Tamil Nadu"
    if re.search(_KL, n):
        return "Kerala"
    return None


def is_dead_isp(email: str) -> bool:
    dom = email.split("@")[-1].lower()
    return dom in _DEAD_ISP or ".vsnl.net" in dom


def split_list(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(";") if x.strip()]


def usable_emails(profile) -> list[str]:
    return [e for e in split_list(profile.emails) if "@" in e and not is_dead_isp(e)]


# ── buying role and persona from a contact's title ─────────────────────

_ROLE_RULES = [
    ("Technical Buyer", r"\b(IT|ICT|SYSTEMS?|NETWORK|COMPUTER|TECHNOLOGY|CTO|CIO|EDP|MIS|INFRASTRUCTURE|AV)\b"),
    ("Procurement", r"\b(PURCHASE|PURCHASING|PROCUREMENT|STORES?|BUYER|SOURCING|TENDER)\b"),
    ("Economic Buyer", r"\b(PRINCIPAL|DIRECTOR|CHAIRMAN|CHAIRPERSON|CORRESPONDENT|CEO|MD|REGISTRAR|SECRETARY|DEAN|OWNER|PRESIDENT|TRUSTEE|FOUNDER|CFO|FINANCE|ACCOUNTS|COMMISSIONER|HEAD OF INSTITUTION)\b"),
    ("User Champion", r"\b(HOD|HEAD OF DEPARTMENT|PROFESSOR|FACULTY|TEACHER|COORDINATOR|LAB|LIBRARIAN|ADMIN|ADMINISTRATOR|MANAGER)\b"),
]
_PERSONA = {"Technical Buyer": "it_head", "Procurement": "procurement",
            "Economic Buyer": "leadership", "User Champion": "operations"}


def buying_role(title: str | None) -> str | None:
    t = (title or "").upper()
    for role, rx in _ROLE_RULES:
        if re.search(rx, t):
            return role
    return None


def persona(title: str | None) -> str:
    """Persona key used for per-persona template overrides
    (TemplateRegistry.segment_overrides_json is keyed by contact_type)."""
    return _PERSONA.get(buying_role(title), "general")


# ── import from the Phase 0 files ──────────────────────────────────────

def import_accounts_csv(db, path: str, pilot_only: bool = False) -> dict:
    """Upsert AccountProfile rows from a Phase 0 CSV (pilot_candidates_* or
    all_unique_accounts). Human-confirmed columns win over name guesses."""
    from app.models.enterprise import AccountProfile
    import os
    added = updated = skipped = 0
    # Addresses that failed the domain check (campaign_phase0/email_check.csv)
    # are never used: no such domain, domain refuses mail, dead ISP, bad syntax.
    bad_email: dict[str, str] = {}
    check = os.path.join(os.path.dirname(path), "email_check.csv")
    if os.path.isfile(check):
        with open(check, encoding="utf-8-sig", newline="") as cf:
            for r in csv.DictReader(cf):
                if r.get("status") not in ("ok", "ok_no_mx_a_record_only"):
                    bad_email[(r.get("email") or "").strip().lower()] = r.get("status")
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            acc_id = (r.get("keep_crm_id") or "").strip()
            name = (r.get("account_name") or "").strip()
            if not acc_id or not name:
                skipped += 1
                continue
            pilot = (r.get("pilot_yes_no") or "").strip().lower() in ("yes", "y", "1", "true")
            if pilot_only and not pilot:
                skipped += 1
                continue
            p = db.get(AccountProfile, acc_id)
            if p is None:
                p = AccountProfile(account_id=acc_id, name=name, norm_name=norm(name))
                db.add(p)
                added += 1
            else:
                updated += 1
            p.name, p.norm_name = name, norm(name)
            region_ism = (r.get("region_confirmed") or "").strip()
            if region_ism:
                p.region, p.region_source = region_ism, "ism"
            elif p.region_source != "ism":
                place = (r.get("place_in_name") or "")
                p.region = "Tamil Nadu" if place.startswith("Tamil") else "Kerala" if place.startswith("Kerala") else region_from_name(name)
                p.region_source = "name" if p.region else None
            if p.vertical_source != "ism":
                vb = (r.get("vertical_by_name") or "").strip()
                p.vertical = "Education" if vb.startswith("Education") else (vb or vertical_from_name(name))
                p.vertical_source = "name" if p.vertical else None
            if (r.get("owner_isr") or "").strip():
                p.owner_isr = r["owner_isr"].strip()
            p.pilot = pilot or p.pilot
            p.website = r.get("website") or p.website
            p.phones = r.get("phones") or p.phones
            all_emails = split_list(r.get("emails"))
            good = [e for e in all_emails if e.lower() not in bad_email]
            dropped = [f"{e} ({bad_email[e.lower()]})" for e in all_emails if e.lower() in bad_email]
            p.emails = "; ".join(good) if all_emails else p.emails
            if dropped:
                note = "Undeliverable, not used: " + ", ".join(dropped)
                p.notes = note if not p.notes else (p.notes if note in p.notes else p.notes + "\n" + note)
            p.email_on_dead_isp = (r.get("email_on_dead_isp") or "").strip().lower() == "yes"
            p.crm_record_count = int(r.get("crm_records") or 1)
            if (r.get("notes") or "").strip():
                p.notes = r["notes"].strip()
            p.updated_ts = datetime.now(timezone.utc)
    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


def load_order_history_csv(db, path: str) -> dict:
    """Load a Tally sales-register export (columns: party_name, gstin, date
    YYYY-MM-DD or DD-MM-YYYY, value). Matches parties to accounts by
    normalised name and stores last order date and value (step 0.14)."""
    from app.models.enterprise import AccountProfile
    by_norm = {p.norm_name: p for p in db.query(AccountProfile).all()}
    latest: dict[str, tuple[datetime, float]] = {}
    unmatched = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            n = norm(r.get("party_name"))
            raw = (r.get("date") or "").strip()
            try:
                d = datetime.strptime(raw, "%Y-%m-%d") if "-" in raw and len(raw.split("-")[0]) == 4 \
                    else datetime.strptime(raw.replace("/", "-"), "%d-%m-%Y")
            except ValueError:
                continue
            try:
                v = float(str(r.get("value") or "0").replace(",", ""))
            except ValueError:
                v = 0.0
            if n not in by_norm:
                unmatched.add(r.get("party_name"))
                continue
            d = d.replace(tzinfo=timezone.utc)
            if n not in latest or d > latest[n][0]:
                latest[n] = (d, v)
    for n, (d, v) in latest.items():
        by_norm[n].last_order_ts, by_norm[n].last_order_value = d, v
    db.commit()
    return {"accounts_with_orders": len(latest), "unmatched_parties": len(unmatched),
            "unmatched_sample": sorted(x for x in unmatched if x)[:20]}


# ── Zoho CRM sync (people, roles, deals) ───────────────────────────────

def _crm_pages(client, module: str, fields: str, max_pages: int = 250):
    token, page = None, 1
    for _ in range(max_pages):
        params = {"fields": fields, "per_page": 200}
        if token:
            params["page_token"] = token
        else:
            params["page"] = page
        data = client._get(module, params)
        for row in data.get("data", []) or []:
            yield row
        info = data.get("info") or {}
        if not info.get("more_records"):
            return
        token = info.get("next_page_token")
        page += 1


def sync_from_crm(db) -> dict:
    """Pull people-contacts (for buying roles) and Deals from Zoho CRM.
    Company-name placeholder contacts (no first name, no email) are ignored.
    Safe to run when the CRM is unreachable: returns the error, changes nothing."""
    from app.integrations.zoho_crm import get_zoho_crm_client
    from app.models.enterprise import AccountProfile, CampaignDeal
    out = {"people": 0, "roles_set": 0, "deals": 0}
    try:
        client = get_zoho_crm_client()
        if not client.configured:
            return {**out, "error": "Zoho CRM not configured"}
        roles: dict[str, dict] = {}
        for c in _crm_pages(client, "Contacts", "First_Name,Last_Name,Email,Title,Account_Name"):
            if not c.get("First_Name") and not c.get("Email"):
                continue  # company-name placeholder
            out["people"] += 1
            acc = c.get("Account_Name")
            acc_id = acc.get("id") if isinstance(acc, dict) else None
            role = buying_role(c.get("Title"))
            if acc_id and role:
                roles.setdefault(acc_id, {}).setdefault(role, []).append(c["id"])
        for acc_id, r in roles.items():
            p = db.get(AccountProfile, acc_id)
            if p is not None:
                p.roles_json = r
                out["roles_set"] += 1
        for d in _crm_pages(client, "Deals", "Deal_Name,Stage,Amount,Probability,Account_Name,Campaign_Source,Closing_Date,Created_Time,Owner"):
            row = db.query(CampaignDeal).filter(CampaignDeal.crm_deal_id == d["id"]).first()
            if row is None:
                row = CampaignDeal(crm_deal_id=d["id"], name=d.get("Deal_Name") or d["id"], stage=d.get("Stage") or "Qualification")
                db.add(row)
            acc = d.get("Account_Name")
            row.account_id = acc.get("id") if isinstance(acc, dict) else row.account_id
            row.name, row.stage = d.get("Deal_Name") or row.name, d.get("Stage") or row.stage
            row.amount = float(d.get("Amount") or 0)
            row.probability = d.get("Probability")
            camp = d.get("Campaign_Source")
            if isinstance(camp, dict) and camp.get("name"):
                row.attribution_json = {**(row.attribution_json or {}), "crm_campaign": camp.get("name")}
            if d.get("Closing_Date"):
                row.expected_close = date.fromisoformat(d["Closing_Date"])
            if d.get("Created_Time"):
                row.created_ts = datetime.fromisoformat(d["Created_Time"])
            out["deals"] += 1
        db.commit()
        refresh_deal_rollups(db)
        return out
    except Exception as exc:
        db.rollback()
        logger.warning("CRM sync failed: %s", exc)
        return {**out, "error": str(exc)[:300]}


def refresh_deal_rollups(db) -> None:
    from app.models.enterprise import AccountProfile, CampaignDeal
    open_ = {}
    for d in db.query(CampaignDeal).all():
        if d.account_id and not d.stage.startswith("Closed"):
            n, v = open_.get(d.account_id, (0, 0.0))
            open_[d.account_id] = (n + 1, v + (d.amount or 0))
    for p in db.query(AccountProfile).all():
        p.open_deal_count, p.open_deal_value = open_.get(p.account_id, (0, 0.0))
    db.commit()


# ── scoring (FR-ACC-04) ────────────────────────────────────────────────

def default_brief() -> dict:
    return {
        "verticals": [v.strip() for v in settings.ism_default_verticals.split(",") if v.strip()],
        "regions": [r.strip() for r in settings.ism_default_regions.split(",") if r.strip()],
    }


def fit_score(p, brief: dict | None = None) -> tuple[int, list[str]]:
    """0-100 with a reason for every point (and for every missing input)."""
    brief = brief or default_brief()
    score, why = 0, []
    if p.vertical and p.vertical in brief.get("verticals", []):
        score += 35; why.append(f"+35 vertical {p.vertical} is targeted ({p.vertical_source or '?'})")
    else:
        why.append(f"+0 vertical {p.vertical or 'unknown'} not targeted")
    if p.region and p.region in brief.get("regions", []):
        pts = 25 if p.region_source == "ism" else 15
        score += pts; why.append(f"+{pts} region {p.region} ({'ISM confirmed' if pts == 25 else 'from name only'})")
    else:
        why.append(f"+0 region {p.region or 'unknown'}")
    if p.last_order_ts:
        score += 15; why.append(f"+15 past customer, last order {p.last_order_ts.date().isoformat()}")
    else:
        why.append("+0 no order history loaded")
    if usable_emails(p):
        score += 15; why.append("+15 has a usable email")
    elif split_list(p.phones):
        score += 8; why.append("+8 phone only")
    else:
        why.append("+0 no usable email or phone")
    roles = (p.roles_json or {}).keys()
    if "Economic Buyer" in roles or "Technical Buyer" in roles:
        score += 10; why.append("+10 economic or technical buyer known")
    else:
        why.append("+0 no economic or technical buyer named")
    return min(score, 100), why


# intent weights per event; each decays 5% per day of age (spec FR-ACC-02)
INTENT_WEIGHTS = {"open": 1, "click": 5, "reply": 15, "positive_reply": 25, "deal_open": 40}
INTENT_DECAY = 0.95


def intent_scores(db, now: datetime | None = None, window_days: int = 60) -> dict[str, float]:
    """Intent per account from the platform's own signals. Contacts map to
    accounts through CampaignContact.extra_json['account_id'] (crm_accounts
    source) or contact_ref == account_id."""
    from app.models.enterprise import CampaignContact, CampaignDeal, EmailEngagementEvent
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)
    ref_to_acc: dict[str, str] = {}
    for c in db.query(CampaignContact).all():
        acc = (c.extra_json or {}).get("account_id") or c.contact_ref
        ref_to_acc[c.contact_ref] = acc
    out: dict[str, float] = {}

    def add(acc, kind, ts):
        if not acc or ts is None:
            return
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < since:
            return
        age = max(0.0, (now - ts).total_seconds() / 86400)
        out[acc] = out.get(acc, 0.0) + INTENT_WEIGHTS[kind] * (INTENT_DECAY ** age)

    opens_counted: dict[str, int] = {}
    for e in db.query(EmailEngagementEvent).filter(EmailEngagementEvent.ts >= since).all():
        acc = ref_to_acc.get(e.contact_ref, e.contact_ref)
        if e.event_type == "open":
            opens_counted[acc] = opens_counted.get(acc, 0) + 1
            if opens_counted[acc] > 3:
                continue  # opens are noisy; at most 3 count
        add(acc, "click" if e.event_type == "click" else "open", e.ts)
    for c in db.query(CampaignContact).filter(CampaignContact.response_ts.isnot(None)).all():
        acc = ref_to_acc.get(c.contact_ref, c.contact_ref)
        if c.response_type == "not_interested":
            continue
        add(acc, "positive_reply" if c.response_type in ("interested", "callback", "converted") else "reply", c.response_ts)
    for d in db.query(CampaignDeal).all():
        if d.account_id and not (d.stage or "").startswith("Closed"):
            add(d.account_id, "deal_open", d.created_ts)
    return {k: round(min(v, 100.0), 1) for k, v in out.items()}


def tier_for(fit: int, intent: float) -> str:
    if fit >= settings.ism_tier_fit_threshold and intent >= settings.ism_tier_intent_threshold:
        return "P1"
    if fit >= settings.ism_tier_fit_threshold or intent >= settings.ism_tier_intent_threshold:
        return "P2"
    return "P3"


def score_all(db, brief: dict | None = None) -> dict:
    from app.models.enterprise import AccountProfile
    now = datetime.now(timezone.utc)
    intents = intent_scores(db, now)
    dist = {"P1": 0, "P2": 0, "P3": 0}
    for p in db.query(AccountProfile).all():
        p.fit_score, p.fit_reasons_json = fit_score(p, brief)
        p.intent_score = intents.get(p.account_id, 0.0)
        p.intent_updated_ts = now
        p.tier = tier_for(p.fit_score, p.intent_score)
        dist[p.tier] += 1
    db.commit()
    return dist


def committee_gap_tasks(db, pilot_only: bool = True) -> int:
    """FR-ACC-03: P1 (or pilot) accounts with no economic/technical buyer
    named get one open research task for their ISR."""
    from app.models.enterprise import AccountProfile
    from app.services.isr_queue import create_task
    q = db.query(AccountProfile).filter(AccountProfile.off_limits.is_(False))
    q = q.filter((AccountProfile.pilot.is_(True)) | (AccountProfile.tier == "P1")) if pilot_only else q.filter(AccountProfile.tier == "P1")
    made = 0
    for p in q.all():
        roles = (p.roles_json or {}).keys()
        missing = [r for r in ("Economic Buyer", "Technical Buyer") if r not in roles]
        if not missing:
            continue
        t, created = create_task(
            db, kind="research", account_id=p.account_id, contact_ref=p.account_id,
            subject=f"Find the {' and '.join(missing)} at {p.name}",
            body=("Name, title, email and mobile for: " + ", ".join(missing) + ".\n"
                  f"Known: phones {p.phones or '-'}; emails {p.emails or '-'}; website {p.website or '-'}.\n"
                  "Use the account's own website/switchboard first; Apollo lookup costs credits."),
            source="committee_gap", priority="Normal", push_crm=False,
        )
        made += int(created)
    db.commit()
    return made


# ── segmentation (FR-SEG-01, 02) and capacity (FR-SEG-03) ──────────────

SEGMENTS = ("accelerate", "reactivate", "high_intent", "nurture")


def segment_for(p, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if p.open_deal_count:
        return "accelerate"
    if p.last_order_ts:
        lo = p.last_order_ts if p.last_order_ts.tzinfo else p.last_order_ts.replace(tzinfo=timezone.utc)
        if (now - lo).days >= 365:
            return "reactivate"
    if p.intent_score >= settings.ism_tier_intent_threshold:
        return "high_intent"
    return "nurture"


def capacity_check(db, campaign) -> dict:
    """Manual touches the sequence will create vs. what the ISRs can do in
    the campaign window. Over capacity -> lowest-priority contacts listed
    for trimming (applied only when the ISM approves with trim)."""
    from app.models.enterprise import AccountProfile, CampaignContact, CampaignSequenceStep
    brief = campaign.ism_brief_json or {}
    contacts = db.query(CampaignContact).filter(CampaignContact.campaign_id == campaign.id).all()
    steps = db.query(CampaignSequenceStep).filter(CampaignSequenceStep.campaign_id == campaign.id).all()
    manual_steps = sum(1 for s in steps if s.channel in ("call", "linkedin"))
    isrs = sorted({(c.extra_json or {}).get("owner_isr") for c in contacts if (c.extra_json or {}).get("owner_isr")}) \
        or list(brief.get("isrs") or [])
    window_days = int(brief.get("window_days") or 60)
    working_days = math.floor(window_days * 5 / 7)
    per_day = int(brief.get("calls_per_isr_per_day") or settings.ism_calls_per_isr_per_day)
    capacity = len(isrs) * per_day * working_days
    demand = len(contacts) * manual_steps
    by_segment: dict[str, int] = {}
    for c in contacts:
        seg = (c.extra_json or {}).get("segment", "unsegmented")
        by_segment[seg] = by_segment.get(seg, 0) + 1
    trim: list[dict] = []
    if manual_steps and demand > capacity:
        over = math.ceil((demand - capacity) / manual_steps)
        def key(c):
            x = c.extra_json or {}
            return (x.get("fit", 0) or 0) + (x.get("intent", 0) or 0)
        for c in sorted(contacts, key=key)[:over]:
            trim.append({"contact_ref": c.contact_ref, "company": c.company,
                         "fit": (c.extra_json or {}).get("fit"), "intent": (c.extra_json or {}).get("intent")})
    return {
        "contacts": len(contacts), "by_segment": by_segment, "manual_steps_per_contact": manual_steps,
        "isrs": isrs, "calls_per_isr_per_day": per_day, "working_days": working_days,
        "capacity_touches": capacity, "demand_touches": demand,
        "over_capacity": demand > capacity, "trim_suggested": trim,
        "note": None if isrs else "No ISR owners on these contacts: set owner_isr in the pilot sheet or isrs in the brief.",
    }
