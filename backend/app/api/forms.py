"""Campaign response form — pre-filled landing page linked in campaign emails.

Each contact in a campaign gets a unique signed URL:
  GET  /forms/{campaign_id}?t=<token>&name=...&email=...&phone=...
    → renders a branded HTML form with their details pre-filled.

  POST /forms/{campaign_id}
    → saves the submission to campaign_form_submission, marks the contact
      as "interested" in campaign_contact, and upserts the data into a
      Zoho CRM Lead (create if new, update if existing).

  GET  /forms/{campaign_id}/submissions
    → internal list of all submissions for a campaign.
"""

import hashlib
import hmac
import logging
import urllib.parse
import uuid as _uuid_mod
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/forms", tags=["forms"])


# ── Token helpers ─────────────────────────────────────────────────────────────

def _verify_form_token(token_str: str) -> tuple[str, str] | None:
    """Verify HMAC-signed token. Returns (campaign_id, contact_ref) or None."""
    try:
        decoded = urllib.parse.unquote(token_str)
        data_part, sig = decoded.rsplit(":", 1)
        secret = settings.approval_hmac_secret or "dev-secret"
        expected = hmac.new(secret.encode(), data_part.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(expected, sig):
            return None
        campaign_id, contact_ref = data_part.split(":", 1)
        return campaign_id, contact_ref
    except Exception:
        return None


def build_form_link(campaign_id: str, contact_ref: str, contact=None) -> str:
    """Build a signed, pre-filled form URL for one campaign contact.

    Called by K3 when rendering the email body so {{ form_link }} resolves
    to a unique URL per recipient with their known details already filled in.
    """
    secret = settings.approval_hmac_secret or "dev-secret"
    token_data = f"{campaign_id}:{contact_ref}"
    sig = hmac.new(secret.encode(), token_data.encode(), hashlib.sha256).hexdigest()[:16]
    token = urllib.parse.quote(f"{token_data}:{sig}", safe="")
    base = settings.approval_base_url.rstrip("/")

    params: dict[str, str] = {"t": token}
    if contact:
        if getattr(contact, "name", None):
            params["name"] = contact.name
        if getattr(contact, "email", None):
            params["email"] = contact.email
        if getattr(contact, "msisdn", None):
            params["phone"] = contact.msisdn
        if getattr(contact, "company", None):
            params["company"] = contact.company

    return f"{base}/forms/{campaign_id}?" + urllib.parse.urlencode(params)


# ── Form config helpers ───────────────────────────────────────────────────────

_DEFAULT_FIELDS = [
    {"name": "name",         "label": "Full Name",               "type": "text",     "required": True,  "enabled": True},
    {"name": "email",        "label": "Email Address",           "type": "email",    "required": True,  "enabled": True},
    {"name": "phone",        "label": "Phone Number",            "type": "tel",      "required": True,  "enabled": True},
    {"name": "address",      "label": "Address",                 "type": "textarea", "required": False, "enabled": True},
    {"name": "requirements", "label": "Requirements / Message",  "type": "textarea", "required": False, "enabled": True},
]

def _merge_form_config(cfg: dict | None) -> dict:
    """Merge a campaign's form_config_json with built-in defaults.

    If the config contains a saved fields list, that order is authoritative
    (supports user reordering and custom fields at any position).
    Default fields not present in the saved list are appended at the end.
    """
    cfg = cfg or {}
    saved_fields = cfg.get("fields", [])
    _default_by_name = {f["name"]: f for f in _DEFAULT_FIELDS}

    if saved_fields:
        fields = []
        seen: set[str] = set()
        for f in saved_fields:
            if not f.get("name"):
                continue
            base = _default_by_name.get(f["name"], {})
            fields.append({**base, **f})
            seen.add(f["name"])
        # Append default fields not yet seen (new defaults added after config was saved)
        for d in _DEFAULT_FIELDS:
            if d["name"] not in seen:
                fields.append(d)
    else:
        fields = list(_DEFAULT_FIELDS)

    return {
        "subtitle":     cfg.get("subtitle", "Please share your details and we'll get back to you shortly."),
        "submit_label": cfg.get("submit_label", "Send My Details →"),
        "brand":        cfg.get("brand", "Yexis Electronics"),
        "fields":       fields,
    }


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _esc(val: str | None) -> str:
    if not val:
        return ""
    return (val.replace("&", "&amp;").replace('"', "&quot;")
               .replace("<", "&lt;").replace(">", "&gt;"))


def _render_field(f: dict, prefill: dict) -> str:
    """Render a single form field from config."""
    name = f["name"]
    label = _esc(f.get("label", name.replace("_", " ").title()))
    req = f.get("required", False)
    req_mark = ' <span class="req">*</span>' if req else ""
    req_attr = " required" if req else ""
    val = _esc(prefill.get(name, ""))
    ftype = f.get("type", "text")

    # Placeholder: use label text, lowercased
    placeholder = label.lower() if ftype != "textarea" else f"Enter your {label.lower()}..."

    if ftype == "textarea":
        return (
            f'<div class="field">'
            f'<label>{label}{req_mark}</label>'
            f'<textarea name="{name}" placeholder="{placeholder}"{req_attr}>{val}</textarea>'
            f'</div>'
        )
    return (
        f'<div class="field">'
        f'<label>{label}{req_mark}</label>'
        f'<input type="{_esc(ftype)}" name="{name}" value="{val}" placeholder="{placeholder}"{req_attr} />'
        f'</div>'
    )


def _form_html(
    campaign_name: str,
    campaign_id: str,
    token: str,
    prefill: dict,
    form_config: dict | None = None,
    error: str | None = None,
) -> str:
    cfg = _merge_form_config(form_config)
    err_block = f'<div class="err">{_esc(error)}</div>' if error else ""
    fields_html = "".join(
        _render_field(f, prefill)
        for f in cfg["fields"]
        if f.get("enabled", True)
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{_esc(campaign_name)} — {_esc(cfg['brand'])}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f5f7fa;
      min-height: 100vh;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 2rem 1rem;
    }}
    .card {{
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 4px 24px rgba(0,0,0,.10);
      padding: 2.5rem 2rem;
      width: 100%;
      max-width: 520px;
    }}
    .brand {{
      font-size: .8rem;
      font-weight: 700;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: #1a56db;
      margin-bottom: .3rem;
    }}
    h1 {{ font-size: 1.35rem; font-weight: 700; color: #111827; margin-bottom: .4rem; }}
    .sub {{ font-size: .88rem; color: #6b7280; margin-bottom: 1.75rem; line-height: 1.5; }}
    label {{ display: block; font-size: .82rem; font-weight: 600; color: #374151; margin-bottom: .3rem; }}
    label .req {{ color: #ef4444; margin-left: .15rem; }}
    input, textarea {{
      width: 100%;
      padding: .6rem .8rem;
      border: 1.5px solid #d1d5db;
      border-radius: 8px;
      font-size: .93rem;
      color: #111827;
      background: #fff;
      transition: border-color .15s;
      font-family: inherit;
    }}
    input:focus, textarea:focus {{
      outline: none;
      border-color: #1a56db;
      box-shadow: 0 0 0 3px rgba(26,86,219,.12);
    }}
    textarea {{ resize: vertical; min-height: 100px; }}
    .field {{ margin-bottom: 1.1rem; }}
    .submit-btn {{
      width: 100%;
      padding: .75rem;
      background: #1a56db;
      color: #fff;
      border: none;
      border-radius: 8px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      margin-top: .5rem;
      transition: background .15s;
    }}
    .submit-btn:hover {{ background: #1648c0; }}
    .submit-btn:active {{ background: #1240a8; }}
    .err {{
      background: #fef2f2;
      border: 1px solid #fecaca;
      color: #b91c1c;
      border-radius: 8px;
      padding: .65rem .9rem;
      font-size: .86rem;
      margin-bottom: 1.25rem;
    }}
    .footer {{ font-size: .72rem; color: #9ca3af; text-align: center; margin-top: 1.5rem; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="brand">{_esc(cfg['brand'])}</div>
    <h1>{_esc(campaign_name)}</h1>
    <p class="sub">{_esc(cfg['subtitle'])}</p>
    {err_block}
    <form method="POST" action="/forms/{_esc(campaign_id)}">
      <input type="hidden" name="t" value="{_esc(token)}" />
      {fields_html}
      <button type="submit" class="submit-btn">{_esc(cfg['submit_label'])}</button>
    </form>
    <div class="footer">{_esc(cfg['brand'])} · {_esc(settings.approval_base_url)}</div>
  </div>
</body>
</html>"""


def _thanks_html(name: str | None, campaign_name: str) -> str:
    greeting = f"Thank you, {_esc(name)}!" if name else "Thank you!"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Details received — Yexis Electronics</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f5f7fa;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem 1rem;
    }}
    .card {{
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 4px 24px rgba(0,0,0,.10);
      padding: 3rem 2rem;
      width: 100%;
      max-width: 480px;
      text-align: center;
    }}
    .icon {{ font-size: 3rem; margin-bottom: 1rem; }}
    h1 {{
      font-size: 1.5rem;
      font-weight: 700;
      color: #111827;
      margin-bottom: .6rem;
    }}
    p {{
      font-size: .95rem;
      color: #6b7280;
      line-height: 1.6;
    }}
    .brand {{
      font-size: .72rem;
      font-weight: 700;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: #1a56db;
      margin-top: 2rem;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>{greeting}</h1>
    <p>Your details for <strong>{_esc(campaign_name)}</strong> have been received.<br>
       Our team will reach out to you shortly.</p>
    <div class="brand">Yexis Electronics</div>
  </div>
</body>
</html>"""


# ── Zoho CRM sync ─────────────────────────────────────────────────────────────

def _sync_to_crm(submission, campaign_name: str) -> str | None:
    """Upsert a Zoho CRM Lead from a form submission. Returns lead id or None."""
    try:
        from app.integrations.zoho_crm import get_zoho_crm_client
        crm = get_zoho_crm_client()
        if not crm.configured:
            return None

        name = (submission.name or "").strip()
        first, _, last = name.rpartition(" ")
        record: dict = {
            "Last_Name": last or name or "Unknown",
            "First_Name": first or None,
            "Email": submission.email,
            "Phone": submission.phone,
            "Mobile": submission.phone,
            "Lead_Source": "Email Campaign Form",
            "Lead_Status": "Contacted",
            "Description": (
                f"Requirements: {submission.requirements or '—'}\n"
                f"Address: {submission.address or '—'}\n"
                f"Campaign: {campaign_name}\n"
                f"Submitted via campaign response form."
            ),
        }
        if submission.address:
            record["Mailing_Street"] = submission.address
        # company from extra_json if present
        company = (submission.extra_json or {}).get("company")
        if company:
            record["Company"] = company
        elif submission.email:
            record["Company"] = submission.email.split("@")[-1]

        # Search Contacts first, then Leads
        found_id = None
        found_module = None
        if submission.email:
            contacts = crm.search_by_email("Contacts", submission.email)
            if contacts:
                found_id, found_module = contacts[0]["id"], "Contacts"
            else:
                leads = crm.search_by_email("Leads", submission.email)
                if leads:
                    found_id, found_module = leads[0]["id"], "Leads"

        if found_id:
            crm.update_record(found_module, found_id, record)
            return found_id
        else:
            return crm.create_record("Leads", record)

    except Exception as exc:
        logger.warning("CRM sync failed for form submission %s: %s", submission.id, exc)
        return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/{campaign_id}", response_class=HTMLResponse)
def form_get(
    campaign_id: str,
    t: str = "",
    name: str = "",
    email: str = "",
    phone: str = "",
    company: str = "",
    db: Session = Depends(get_db),
):
    """Render the pre-filled form. The signed token proves the link came
    from a campaign email — without it we show the form anyway (best effort)
    but won't be able to tie the submission to a campaign contact."""
    from app.models.enterprise import Campaign

    campaign = db.get(Campaign, campaign_id)
    campaign_name = campaign.name if campaign else "Campaign Response"
    form_config = campaign.form_config_json if campaign else None

    prefill = {k: v for k, v in {"name": name, "email": email, "phone": phone}.items() if v}
    return HTMLResponse(_form_html(campaign_name, campaign_id, t, prefill, form_config=form_config))


@router.post("/{campaign_id}", response_class=HTMLResponse)
async def form_post(
    campaign_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Handle form submission: save to DB, mark contact responded, sync CRM."""
    from app.models.enterprise import Campaign, CampaignContact, CampaignFormSubmission

    form = await request.form()
    token = str(form.get("t") or "")
    name = str(form.get("name") or "").strip()
    email_val = str(form.get("email") or "").strip().lower()
    phone = str(form.get("phone") or "").strip()
    address = str(form.get("address") or "").strip()
    requirements = str(form.get("requirements") or "").strip()

    # Load campaign once for validation error responses
    campaign = db.get(Campaign, campaign_id)
    campaign_name = campaign.name if campaign else "Campaign Response"
    form_config = campaign.form_config_json if campaign else None
    cfg = _merge_form_config(form_config)
    required_names = {f["name"] for f in cfg["fields"] if f.get("required") and f.get("enabled", True)}

    def _err(msg):
        prefill = {"name": name, "email": email_val, "phone": phone,
                   "address": address, "requirements": requirements}
        return HTMLResponse(
            _form_html(campaign_name, campaign_id, token, prefill,
                       form_config=form_config, error=msg),
            status_code=422,
        )

    if "name" in required_names and not name:
        return _err("Full name is required.")
    if "email" in required_names and (not email_val or "@" not in email_val):
        return _err("A valid email address is required.")
    if "phone" in required_names and not phone:
        return _err("Phone number is required.")

    # Resolve the contact_ref from the signed token (best-effort)
    parsed = _verify_form_token(token)
    contact_ref = parsed[1] if parsed else email_val

    # Persist submission
    now = datetime.now(timezone.utc)
    submission = CampaignFormSubmission(
        campaign_id=_uuid_mod.UUID(campaign_id),
        contact_ref=contact_ref,
        submitted_at=now,
        name=name,
        email=email_val,
        phone=phone,
        address=address or None,
        requirements=requirements or None,
    )
    db.add(submission)
    db.flush()  # get the id before CRM call

    # Mark campaign contact as interested (if found)
    contact = db.query(CampaignContact).filter(
        CampaignContact.campaign_id == _uuid_mod.UUID(campaign_id),
        CampaignContact.contact_ref == contact_ref,
    ).first()
    if contact and not contact.response_ts:
        contact.response_ts = now
        contact.response_type = "interested"
        # Backfill any newly supplied details onto the contact row
        if name and not contact.name:
            contact.name = name
        if email_val and not contact.email:
            contact.email = email_val
        if phone and not contact.msisdn:
            contact.msisdn = phone

    # Sync to Zoho CRM (best-effort — never blocks the response)
    zoho_id = _sync_to_crm(submission, campaign_name)
    if zoho_id:
        submission.zoho_lead_id = zoho_id
        submission.zoho_sync_ts = datetime.now(timezone.utc)
    else:
        submission.zoho_error = "sync skipped or failed — see server logs"

    db.commit()
    logger.info(
        "Form submission: campaign=%s contact=%s zoho_lead=%s",
        campaign_id, contact_ref, zoho_id or "—",
    )

    return HTMLResponse(_thanks_html(name, campaign_name))


@router.get("/{campaign_id}/submissions")
def list_submissions(
    campaign_id: str,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List all form submissions for a campaign (internal / admin use)."""
    from app.models.enterprise import CampaignFormSubmission
    from fastapi.responses import JSONResponse

    rows = (
        db.query(CampaignFormSubmission)
        .filter(CampaignFormSubmission.campaign_id == _uuid_mod.UUID(campaign_id))
        .order_by(CampaignFormSubmission.submitted_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "contact_ref": r.contact_ref,
            "submitted_at": r.submitted_at.isoformat(),
            "name": r.name,
            "email": r.email,
            "phone": r.phone,
            "address": r.address,
            "requirements": r.requirements,
            "zoho_lead_id": r.zoho_lead_id,
            "zoho_sync_ts": r.zoho_sync_ts.isoformat() if r.zoho_sync_ts else None,
            "zoho_error": r.zoho_error,
        }
        for r in rows
    ]


@router.get("/{campaign_id}/config")
def get_form_config(campaign_id: str, db: Session = Depends(get_db)):
    """Return the merged form config (defaults + any saved overrides)."""
    from app.models.enterprise import Campaign
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        from fastapi import HTTPException
        raise HTTPException(404, "Campaign not found")
    return _merge_form_config(campaign.form_config_json)


@router.patch("/{campaign_id}/config")
def update_form_config(campaign_id: str, body: dict, db: Session = Depends(get_db)):
    """Save form customisation for a campaign.

    Accepted body fields (all optional):
      subtitle      — text shown below the campaign title
      submit_label  — text on the submit button
      brand         — company name shown at top and bottom
      fields        — list of field overrides:
        [{ "name": "address", "enabled": false }, ...]
        [{ "name": "requirements", "label": "Your Message", "required": true }, ...]
        [{ "name": "custom_field", "label": "Company Size", "type": "text",
           "required": false, "enabled": true }, ...]

    Only the keys you provide are merged; everything else stays at its default.
    """
    from app.models.enterprise import Campaign
    from fastapi import HTTPException
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")

    allowed = {"subtitle", "submit_label", "brand", "fields"}
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown fields: {', '.join(sorted(unknown))}")

    existing = dict(campaign.form_config_json or {})

    if "subtitle" in body:
        existing["subtitle"] = str(body["subtitle"]).strip()
    if "submit_label" in body:
        existing["submit_label"] = str(body["submit_label"]).strip()
    if "brand" in body:
        existing["brand"] = str(body["brand"]).strip()
    if "fields" in body:
        if not isinstance(body["fields"], list):
            raise HTTPException(400, "'fields' must be a list")
        # Store the full ordered list sent by the client
        fields_out = []
        for f in body["fields"]:
            if not isinstance(f, dict) or "name" not in f:
                raise HTTPException(400, "Each field entry must have a 'name' key")
            fields_out.append(f)
        existing["fields"] = fields_out

    campaign.form_config_json = existing
    db.commit()
    return _merge_form_config(existing)
