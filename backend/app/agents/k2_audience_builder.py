"""K2 Audience Builder — multi-source dealer/contact audience selection.

Autonomy: A2 (supervised — audience selection is automatic; Marketing Manager
reviews before broadcast is triggered).

Sources:
  dealermart  — existing dealers in local DealerMart (Zoho Books offtake data)
  zoho_books  — live Zoho Books customers
  zoho_crm    — live Zoho CRM accounts + contacts
  apollo      — prospect search via Apollo.io REST API
  all         — all 4 sources, deduplicated by contact_ref

Actions:
  build   — select contacts from one or more sources into CampaignContact
  enrich  — fill missing msisdn/email on existing contacts via RocketReach
  report  — audience composition for a campaign
  clear   — remove all contacts from a draft campaign
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"build", "enrich", "report", "clear"}
VALID_SOURCES = {"dealermart", "zoho_books", "zoho_crm", "apollo", "excel", "crm_accounts", "all"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL", "YEXIS-SOL"}
VALID_ENRICHERS = {"rocketreach", "apollo", "both"}

logger = logging.getLogger(__name__)


# ── Phone normalisation ───────────────────────────────────────────────

def _normalise_phone(raw: str | None) -> str | None:
    """Normalise to E.164 India (digits only, leading 91, no +).

    Returns None for obviously dirty data (concatenated numbers > 15 digits).
    """
    if not raw:
        return None
    # Take only the first phone-like token — handles "9876543210 / 9123456789"
    first_token = re.split(r"[,;/\s]+", raw.strip())[0]
    digits = re.sub(r"[^\d]", "", first_token)
    if not digits:
        return None
    if digits.startswith("0"):
        digits = "91" + digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) < 10 or len(digits) > 15:
        return None
    return digits


# ── Source fetchers ───────────────────────────────────────────────────

def _fetch_crm_accounts(db, payload: dict) -> tuple[list[dict], str | None]:
    """ISM Campaign Assistant source: scored AccountProfile rows (FR-SEG-01).

    Filters: regions, verticals, tiers, owner_isr (lists or comma strings),
    pilot_only (default True). Off-limits accounts are never included.
    Each account becomes one contact (its best usable email/phone), tagged
    with segment, persona, tier, scores and owner in extra_json. Accounts
    with neither a usable email nor a phone are skipped and counted.
    """
    from app.models.enterprise import AccountProfile
    from app.services.account_intel import segment_for, split_list, usable_emails

    def _lst(v):
        if not v:
            return []
        return [x.strip() for x in (v.split(",") if isinstance(v, str) else v) if str(x).strip()]

    q = db.query(AccountProfile).filter(AccountProfile.off_limits.is_(False))
    if payload.get("pilot_only", True):
        q = q.filter(AccountProfile.pilot.is_(True))
    for col, key in ((AccountProfile.region, "regions"), (AccountProfile.vertical, "verticals"),
                     (AccountProfile.tier, "tiers"), (AccountProfile.owner_isr, "owner_isr")):
        vals = _lst(payload.get(key))
        if vals:
            q = q.filter(col.in_(vals))
    rows, unreachable = [], 0
    for p in q.all():
        emails = usable_emails(p)
        phones = [re.sub(r"[^\d]", "", x) for x in split_list(p.phones)]
        msisdn = next((m for m in (_normalise_phone(x) for x in phones) if m), None)
        if not emails and not msisdn:
            unreachable += 1
            continue
        rows.append({
            "contact_ref": p.account_id,
            "name": None,
            "company": p.name,
            "email": emails[0] if emails else None,
            "alt_email": emails[1] if len(emails) > 1 else None,
            "msisdn": msisdn,
            "contact_type": "general",
            "extra": {
                "account_id": p.account_id, "segment": segment_for(p), "persona": "general",
                "tier": p.tier, "fit": p.fit_score, "intent": p.intent_score,
                "owner_isr": p.owner_isr, "vertical": p.vertical, "region": p.region,
                # No named person yet for most institutions: templates greet with
                # {{ greeting }} instead of {{ name }} (which would fall back to an id).
                "greeting": "Sir/Madam",
            },
        })
    _assign_waves(rows, int(payload.get("waves") or 0))
    err = f"{unreachable} accounts skipped: no usable email or phone" if unreachable else None
    return rows, err


def _assign_waves(rows: list[dict], waves: int) -> None:
    """Split the audience into `waves` of near-equal size, best accounts
    first and balanced across ISRs (each wave gets about the same number per
    ISR). Stored as extra['wave']; K3 only sends to released waves."""
    if waves <= 1:
        return
    import math
    rank = {"P1": 0, "P2": 1, "P3": 2}
    by_owner: dict[str, list[dict]] = {}
    for r in rows:
        by_owner.setdefault(r["extra"].get("owner_isr") or "", []).append(r)
    for lst in by_owner.values():
        lst.sort(key=lambda r: (rank.get(r["extra"].get("tier"), 3),
                                -((r["extra"].get("fit") or 0) + (r["extra"].get("intent") or 0))))
    ordered, i = [], 0
    while any(by_owner.values()):
        for k in sorted(by_owner):
            if i < len(by_owner[k]):
                ordered.append(by_owner[k][i])
        i += 1
        if all(i >= len(v) for v in by_owner.values()):
            break
    size = math.ceil(len(ordered) / waves) if ordered else 0
    for n, r in enumerate(ordered):
        r["extra"]["wave"] = n // size + 1 if size else 1


def _fetch_dealermart(db, branch: str | None, min_score: float | None) -> tuple[list[dict], str | None]:
    """Pull contacts from local DealerMart, joining WaContact for phone number."""
    from app.models.enterprise import DealerMart, WaContact
    try:
        q = db.query(DealerMart)
        if branch:
            q = q.filter(DealerMart.branch == branch)
        if min_score is not None:
            q = q.filter(DealerMart.payment_behavior_score >= min_score)
        rows = q.all()

        # Build a lookup of contact_ref -> msisdn from WaContact
        dealer_refs = [d.dealer_ref for d in rows]
        wa_lookup: dict[str, str] = {}
        if dealer_refs:
            wa_rows = db.query(WaContact).filter(
                WaContact.contact_ref.in_(dealer_refs),
                WaContact.opt_in_status == "opt_in",
            ).all()
            for w in wa_rows:
                if w.contact_ref:
                    wa_lookup[w.contact_ref] = w.msisdn

        contacts = []
        for d in rows:
            contacts.append({
                "contact_ref": f"dealer_{d.dealer_ref}",
                "msisdn": wa_lookup.get(d.dealer_ref),
                "email": None,
            })
        return contacts, None
    except Exception as exc:
        return [], str(exc)


def _fetch_zoho_books(page_limit: int = 5) -> tuple[list[dict], str | None]:
    """Pull customers from Zoho Books."""
    try:
        from app.integrations.zoho_books import get_zoho_client
        client = get_zoho_client()
        if not client.configured:
            return [], "Zoho Books not configured"
        raw = client.list_contacts(contact_type="customer")
        contacts = []
        for c in raw:
            cid = c.get("contact_id") or c.get("contact_name", "")
            contacts.append({
                "contact_ref": f"books_{cid}",
                "msisdn": _normalise_phone(c.get("phone") or c.get("mobile")),
                "email": c.get("email"),
            })
        return contacts, None
    except Exception as exc:
        logger.warning("Zoho Books source error: %s", exc)
        return [], str(exc)


def _fetch_zoho_crm() -> tuple[list[dict], str | None]:
    """Pull accounts + contacts from Zoho CRM."""
    try:
        from app.integrations.zoho_crm import get_zoho_crm_client, ZohoCrmError
        client = get_zoho_crm_client()
        if not client.configured:
            return [], "Zoho CRM not configured"
        contacts = []
        # Accounts (companies / dealer businesses)
        try:
            accounts = client.list_accounts()
            for a in accounts:
                aid = a.get("id") or a.get("Account_Name", "")
                contacts.append({
                    "contact_ref": f"crm_{aid}",
                    "msisdn": _normalise_phone(a.get("Phone")),
                    "email": a.get("Email"),
                })
        except ZohoCrmError as exc:
            logger.warning("CRM accounts fetch failed: %s", exc)

        # Contacts (individual people at those accounts)
        try:
            people = client.list_contacts()
            for p in people:
                pid = p.get("id") or ""
                contacts.append({
                    "contact_ref": f"crm_contact_{pid}",
                    "msisdn": _normalise_phone(p.get("Mobile") or p.get("Phone")),
                    "email": p.get("Email"),
                })
        except ZohoCrmError as exc:
            logger.warning("CRM contacts fetch failed: %s", exc)

        return contacts, None
    except Exception as exc:
        logger.warning("Zoho CRM source error: %s", exc)
        return [], str(exc)


def _fetch_apollo(
    keywords: str | None,
    location: str | None,
    title: str | None,
    industry: str | None,
    per_page: int,
) -> tuple[list[dict], str | None]:
    """Search new prospects via Apollo REST."""
    try:
        from app.integrations.apollo_rest import search_people
        people = search_people(
            keywords=keywords,
            location=location,
            title=title,
            industry=industry,
            per_page=per_page,
        )
        contacts = []
        for p in people:
            aid = p.get("apollo_id") or p.get("name", "unknown")
            contacts.append({
                "contact_ref": f"apollo_{aid}",
                "msisdn": _normalise_phone(p.get("phone")),
                "email": p.get("email"),
            })
        return contacts, None
    except Exception as exc:
        logger.warning("Apollo source error: %s", exc)
        return [], str(exc)


# ── Excel/CSV column mapping ──────────────────────────────────────────
#
# Two ways to turn a spreadsheet into contacts:
#   1. Auto (mapping=None) — headers are matched to fields by a synonym list,
#      and every unrecognised column becomes a template variable. Zero clicks.
#   2. Explicit (mapping given) — the UI shows the user each detected column
#      pre-filled with the auto guess and lets them re-tag it (Zoho-style),
#      then passes back {column_index: target}. This is the authoritative path.
#
# Targets a column can map to:
#   name | first_name | email | phone | company | ignore | <custom var name>
# Multiple columns may share email/phone: the first non-empty becomes the
# primary (email→email, phone→msisdn), the second an alternate
# (email→alt_email, phone→phone_2), and any further ones spill into extra
# vars (email_3, phone_3, …) so nothing an operator mapped is silently lost.

# Standard targets that are NOT free-form custom variable names.
_STD_TARGETS = {"name", "first_name", "email", "phone", "company", "ignore"}

_NAME_COLS    = {
    "name", "contact_name", "full_name", "contact", "contact_person",
    "person_name", "client_name", "customer_name",
}
_FIRST_COLS   = {"first_name", "firstname", "first", "given_name"}
_EMAIL_COLS   = {
    "email", "email_address", "e_mail", "mail", "alternate_email",
    "alt_email", "secondary_email", "contact_email", "email_id",
    "emailid", "business_email", "work_email",
}
_PHONE_COLS   = {
    "mobile", "phone", "phone_number", "msisdn", "direct", "board",
    "mobile_number", "contact_number", "whatsapp", "landline", "telephone",
    "contact_phone", "contact_mobile", "mobile_no", "phone_no", "ph_no",
    "board_phone", "office_phone", "work_phone", "mob", "ph",
    "cell", "cell_phone",
}
_COMPANY_COLS = {
    "company", "account_name", "organisation", "organization", "account",
    "firm", "company_name", "business_name", "account_key",
}


def _norm_header(s) -> str:
    """Normalise a header cell: lowercase, spaces/underscores → single '_'."""
    return re.sub(r"[\s_]+", "_", str(s).strip().lower()) if s is not None else ""


def _guess_target(normalized_header: str) -> str:
    """Best-guess field for a normalised header. Unknown → the header itself
    (used as a custom template-variable name); blank header → 'ignore'."""
    h = normalized_header
    if not h:
        return "ignore"
    if h in _NAME_COLS:
        return "name"
    if h in _FIRST_COLS:
        return "first_name"
    if h in _EMAIL_COLS:
        return "email"
    if h in _PHONE_COLS:
        return "phone"
    if h in _COMPANY_COLS:
        return "company"
    return h  # custom variable, named after the column


def _find_header_row(rows: list) -> int:
    """Return the 0-based index of the actual header row.

    Handles files with a title row + blank row before the real headers.
    Scans the first 10 rows and returns the first one where at least 25%
    of columns (or at least 2) are non-empty.
    """
    if not rows:
        return 0
    n_cols = max((len(r) for r in rows[:10]), default=1)
    threshold = max(2, n_cols // 4)
    for i, row in enumerate(rows[:10]):
        non_empty = sum(1 for v in row if v is not None and str(v).strip())
        if non_empty >= threshold:
            return i
    return 0


def _score_sheet_for_contacts(rows: list) -> int:
    """Score how useful a sheet is as a contact list. Higher = more useful.
    Used to auto-pick the best sheet from a multi-sheet workbook."""
    if not rows:
        return 0
    header_idx = _find_header_row(rows)
    if header_idx >= len(rows):
        return 0
    score = 0
    for v in rows[header_idx]:
        t = _guess_target(_norm_header(v))
        if t == "email":                   score += 10
        elif t == "phone":                 score += 8
        elif t in ("name", "first_name"):  score += 5
        elif t == "company":               score += 2
    data_rows = max(0, len(rows) - header_idx - 1)
    score += min(data_rows // 10, 10)
    return score


def _read_rows(
    file_path: str, sheet: str | None = None
) -> tuple[list, str | None, str | None]:
    """Read an Excel/CSV file. Returns (rows, error, chosen_sheet_name).

    rows always starts at the detected header row (title/blank preamble skipped).
    sheet=None → auto-picks the sheet with the highest contact-list score.
    sheet="Contacts" → reads that specific sheet.
    CSV files return chosen_sheet_name=None.
    """
    try:
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        if ext == "csv":
            import csv
            with open(file_path, newline="", encoding="utf-8-sig") as fh:
                rows = [tuple(row) for row in csv.reader(fh)]
            header_idx = _find_header_row(rows)
            return rows[header_idx:], None, None

        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        # Read all sheets once — they're small enough
        all_rows: dict[str, list] = {}
        for name in wb.sheetnames:
            ws = wb[name]
            all_rows[name] = list(ws.iter_rows(values_only=True))
        wb.close()

        if sheet and sheet in all_rows:
            chosen = sheet
        else:
            # Auto-pick: highest contact-relevance score
            chosen = max(all_rows, key=lambda n: _score_sheet_for_contacts(all_rows[n]))

        rows = all_rows[chosen]
        header_idx = _find_header_row(rows)
        return rows[header_idx:], None, chosen
    except Exception as exc:
        return [], f"Could not read file: {exc}", None


def _list_sheets_info(file_path: str) -> list[dict]:
    """Return metadata for each sheet in a workbook (name, row count, score).
    Powers the sheet-picker in the column mapping UI. Returns [] for CSV."""
    try:
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        if ext == "csv":
            return []
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        result = []
        for name in wb.sheetnames:
            ws = wb[name]
            rows = list(ws.iter_rows(values_only=True))
            header_idx = _find_header_row(rows)
            data_rows = max(0, len(rows) - header_idx - 1)
            result.append({
                "name": name,
                "rows": data_rows,
                "score": _score_sheet_for_contacts(rows),
            })
        wb.close()
        return result
    except Exception:
        return []


def parse_excel_preview(
    file_path: str, sample_n: int = 4, sheet: str | None = None
) -> tuple[dict | None, str | None]:
    """Inspect a file WITHOUT committing. Returns columns with auto-guessed
    targets + sample values, total row count, chosen sheet, and sheet list.
    Powers the mapping screen before import.
    """
    rows, err, chosen_sheet = _read_rows(file_path, sheet=sheet)
    if err:
        return None, err
    if not rows:
        return None, "File is empty"

    raw_header = list(rows[0])
    columns = []
    for i, raw in enumerate(raw_header):
        normalized = _norm_header(raw)
        samples = []
        for r in rows[1:1 + sample_n]:
            v = r[i] if i < len(r) else None
            s = str(v).strip() if v is not None else ""
            if s:
                samples.append(s)
        columns.append({
            "index": i,
            "header": (str(raw).strip() if raw is not None else "") or f"Column {i + 1}",
            "normalized": normalized,
            "guess": _guess_target(normalized),
            "samples": samples,
        })
    return {
        "columns": columns,
        "total_rows": max(0, len(rows) - 1),
        "sheet": chosen_sheet,
        "sheets": _list_sheets_info(file_path),
    }, None


def _extract_emails(raw: str | None) -> list[str]:
    """Split a cell into valid, lower-cased email addresses (one cell may hold
    several, separated by , ; / or whitespace)."""
    if not raw or "@" not in raw:
        return []
    out = []
    for part in re.split(r"[,;/\s]+", raw):
        part = part.strip().lower()
        if "@" in part and "." in part.split("@")[-1] and part not in out:
            out.append(part)
    return out


def _fetch_excel(
    file_path: str,
    mapping: dict | None = None,
    sheet: str | None = None,
) -> tuple[list[dict], str | None]:
    """Parse an Excel/CSV file into campaign contacts.

    mapping: optional {column_index: target}. When omitted, targets are
    auto-guessed from headers. Targets: name | first_name | email | phone |
    company | ignore | <custom variable name>.

    sheet: optional sheet name for multi-sheet workbooks. When omitted the
    sheet with the highest contact-list score is used automatically.

    Multiple email columns → email (primary), alt_email, then email_3…
    Multiple phone columns → msisdn (primary), then phone_2, phone_3…
    Custom-named columns become {{ variables }} for the email template.
    Rows with neither an email nor a phone are skipped.
    """
    rows, err, _ = _read_rows(file_path, sheet=sheet)
    if err:
        return [], err
    if not rows:
        return [], "File is empty"

    header = [_norm_header(h) for h in rows[0]]

    # Resolve the mapping: caller-supplied wins, else auto-guess per column.
    if mapping:
        resolved = {int(k): str(v) for k, v in mapping.items()}
    else:
        resolved = {i: _guess_target(h) for i, h in enumerate(header)}

    name_cols    = [i for i, t in resolved.items() if t == "name"]
    first_cols   = [i for i, t in resolved.items() if t == "first_name"]
    email_cols   = sorted(i for i, t in resolved.items() if t == "email")
    phone_cols   = sorted(i for i, t in resolved.items() if t == "phone")
    company_cols = [i for i, t in resolved.items() if t == "company"]
    custom_cols  = [(i, t) for i, t in resolved.items()
                    if t and t not in _STD_TARGETS]

    if not email_cols and not phone_cols:
        return [], (
            "No column is mapped to Email or Phone — map at least one so "
            "contacts can be reached."
        )

    contacts = []
    for row_idx, row in enumerate(rows[1:], start=2):
        def _cell(ci):
            if ci is None or ci >= len(row):
                return None
            v = row[ci]
            s = str(v).strip() if v is not None else None
            return s or None

        def _first(cis):
            for ci in cis:
                v = _cell(ci)
                if v:
                    return v
            return None

        raw_name    = _first(name_cols) or ""
        raw_company = _first(company_cols) or ""

        # ── Emails: gather across all email-mapped columns (+ multi-per-cell) ──
        all_emails: list[str] = []
        for ci in email_cols:
            for e in _extract_emails(_cell(ci)):
                if e not in all_emails:
                    all_emails.append(e)
        email     = all_emails[0] if all_emails else None
        alt_email = all_emails[1] if len(all_emails) > 1 else None

        # ── Phones: gather across all phone-mapped columns ──
        all_phones: list[str] = []
        for ci in phone_cols:
            raw = _cell(ci)
            if raw and raw.startswith("'"):   # Excel adds ' before +country code
                raw = raw[1:]
            p = _normalise_phone(raw)
            if p and p not in all_phones:
                all_phones.append(p)
        phone = all_phones[0] if all_phones else None

        if not email and not phone:
            continue

        # ── Extra template variables ──
        extra: dict[str, str] = {}
        fn = _first(first_cols)
        if fn:
            extra["first_name"] = fn
        for i, t in custom_cols:
            v = _cell(i)
            if v:
                extra[t] = v
        # Spill-over emails/phones beyond primary+alternate stay available.
        for n, e in enumerate(all_emails[2:], start=3):
            extra[f"email_{n}"] = e
        for n, p in enumerate(all_phones[1:], start=2):
            extra[f"phone_{n}"] = p

        # Email is the most stable dedup key; fall back to name+row otherwise.
        if email:
            ref_base = email
        else:
            name_slug = re.sub(r"\s+", "_", raw_name.lower()) if raw_name else f"row{row_idx}"
            ref_base = f"{name_slug}_{row_idx}"
        ref_safe = re.sub(r"[^a-zA-Z0-9_@.\-]", "_", ref_base)

        contacts.append({
            "contact_ref": f"excel_{ref_safe}",
            "msisdn": phone,
            "email": email,
            "alt_email": alt_email,
            "name": raw_name,
            "company": raw_company,
            "extra": extra,
        })

    return contacts, None


# ── Agent ─────────────────────────────────────────────────────────────

class K2AudienceBuilder(BaseAgent):
    """Audience Builder — K2

    Builds campaign audience from Zoho Books customers, Zoho CRM accounts,
    Apollo.io prospects, and/or the local DealerMart. Enriches missing contact
    details via RocketReach.
    """

    agent_code = "K2"
    agent_name = "Audience Builder"

    def describe(self) -> str:
        return (
            "Multi-source campaign audience builder: DealerMart, Zoho Books, "
            "Zoho CRM, Apollo.io prospecting, with RocketReach enrichment."
        )

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
        if action == "build":
            src = payload.get("source", "dealermart")
            if src not in VALID_SOURCES:
                return f"source must be one of {sorted(VALID_SOURCES)}"
            if src == "excel" and not payload.get("file_path"):
                return "'file_path' is required when source is 'excel'"
        if action == "enrich":
            enricher = payload.get("enricher", "rocketreach")
            if enricher not in VALID_ENRICHERS:
                return f"enricher must be one of {sorted(VALID_ENRICHERS)}"
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

        if action == "build":
            return self._build(db, payload, campaign, campaign_id, CampaignContact)

        if action == "enrich":
            return self._enrich(db, campaign, campaign_id, CampaignContact,
                                enricher=payload.get("enricher", "rocketreach"))

        if action == "clear":
            return self._clear(db, campaign, campaign_id, CampaignContact)

        if action == "report":
            return self._report(db, campaign, campaign_id, CampaignContact)

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")

    # ── build ─────────────────────────────────────────────────────────

    def _build(self, db, payload, campaign, campaign_id, CampaignContact) -> AgentResult:
        if campaign.status not in ("draft", "audience_built"):
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Campaign must be in draft/audience_built to build audience (current: {campaign.status})",
            )

        source = payload.get("source", "dealermart")
        branch = payload.get("branch")
        min_score = payload.get("min_payment_score")
        keywords = payload.get("keywords")
        location = payload.get("location")
        title = payload.get("title")
        industry = payload.get("industry")
        per_page = int(payload.get("per_page", 50))
        file_path = payload.get("file_path")

        all_contacts: list[dict] = []
        source_errors: dict[str, str] = {}

        run = [source] if source != "all" else ["dealermart", "zoho_books", "zoho_crm", "apollo"]

        for src in run:
            if src == "dealermart":
                rows, err = _fetch_dealermart(db, branch, float(min_score) if min_score is not None else None)
            elif src == "zoho_books":
                rows, err = _fetch_zoho_books()
            elif src == "zoho_crm":
                rows, err = _fetch_zoho_crm()
            elif src == "apollo":
                rows, err = _fetch_apollo(keywords, location, title, industry, per_page)
            elif src == "excel":
                rows, err = _fetch_excel(file_path or "")
            elif src == "crm_accounts":
                rows, err = _fetch_crm_accounts(db, payload)
            else:
                continue

            if err:
                source_errors[src] = err
                logger.warning("K2 source=%s error: %s", src, err)
            all_contacts.extend(rows)

        # Deduplicate by contact_ref within this batch
        seen: set[str] = set()
        unique: list[dict] = []
        for c in all_contacts:
            ref = c["contact_ref"]
            if ref not in seen:
                seen.add(ref)
                unique.append(c)

        # Upsert into CampaignContact
        added = skipped = 0
        for c in unique:
            existing = db.query(CampaignContact).filter(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.contact_ref == c["contact_ref"],
            ).first()
            if existing:
                # Update phone/email if we have better data now
                if c.get("msisdn") and not existing.msisdn:
                    existing.msisdn = c["msisdn"]
                if c.get("email") and not existing.email:
                    existing.email = c["email"]
                if c.get("company") and not existing.company:
                    existing.company = c["company"]
                if c.get("extra") and not existing.extra_json:
                    existing.extra_json = c["extra"]
                skipped += 1
                continue

            db.add(CampaignContact(
                campaign_id=campaign.id,
                contact_ref=c["contact_ref"],
                contact_type=c.get("contact_type") or "dealer",
                msisdn=c.get("msisdn"),
                email=c.get("email"),
                alt_email=c.get("alt_email") or None,
                name=c.get("name") or None,
                company=c.get("company") or None,
                extra_json=c.get("extra") or None,
            ))
            added += 1

        campaign.status = "audience_built"
        db.commit()

        total = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id
        ).count()

        return AgentResult(
            status="SUCCESS",
            output={
                "campaign_id": campaign_id,
                "campaign_name": campaign.name,
                "source": source,
                "sources_run": run,
                "contacts_fetched": len(all_contacts),
                "added": added,
                "skipped_existing": skipped,
                "total_audience": total,
                "source_errors": source_errors,
                "campaign_status": "audience_built",
                "next_step": "Run 'enrich' action to fill missing msisdn/email via RocketReach, then Marketing Manager approves K3 broadcast.",
            },
            actions_taken=[f"audience_built_{added}_contacts_from_{source}"],
        )

    # ── enrich ────────────────────────────────────────────────────────

    def _enrich(self, db, campaign, campaign_id, CampaignContact, enricher: str = "rocketreach") -> AgentResult:
        """Fill missing msisdn/email on existing contacts.

        enricher:
          rocketreach (default) — uses RocketReach lookupProfile
          apollo                — uses Apollo people/match
          both                  — tries RocketReach first, falls back to Apollo
        """
        from app.integrations import rocketreach_client as rr
        from app.integrations.apollo_rest import match_person as apollo_match

        if not campaign_id:
            return AgentResult(status="VALIDATION_ERROR", error="campaign_id required")

        incomplete = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id,
        ).filter(
            (CampaignContact.msisdn == None) | (CampaignContact.email == None)  # noqa: E711
        ).all()

        if not incomplete:
            return AgentResult(
                status="SUCCESS",
                output={"campaign_id": campaign_id, "enriched": 0, "already_complete": "all contacts have msisdn and email"},
                actions_taken=[],
            )

        use_rr = enricher in ("rocketreach", "both")
        use_apollo = enricher in ("apollo", "both")

        enriched = not_found = skipped = 0

        for contact in incomplete:
            already_has_phone = bool(contact.msisdn)
            already_has_email = bool(contact.email)

            if already_has_phone and already_has_email:
                skipped += 1
                continue

            profile = None

            if use_rr:
                profile = rr.lookup_person(name=contact.contact_ref, company="")

            if profile is None and use_apollo:
                # contact_ref for excel contacts is "excel_<email or phone>" —
                # strip the prefix to get a cleaner lookup name
                raw_name = contact.contact_ref
                if raw_name.startswith("excel_"):
                    raw_name = raw_name[6:]
                profile = apollo_match(name=raw_name, company="")

            if not profile:
                not_found += 1
                continue

            updated = False
            if not already_has_phone and profile.get("phone"):
                norm = _normalise_phone(profile["phone"])
                if norm:
                    contact.msisdn = norm
                    updated = True
            if not already_has_email and profile.get("email"):
                contact.email = profile["email"]
                updated = True

            if updated:
                enriched += 1
            else:
                not_found += 1

        db.commit()

        return AgentResult(
            status="SUCCESS",
            output={
                "campaign_id": campaign_id,
                "campaign_name": campaign.name,
                "enricher": enricher,
                "enriched": enriched,
                "not_found": not_found,
                "already_complete": skipped,
                "total_checked": len(incomplete),
            },
            actions_taken=[f"enriched_{enriched}_contacts_via_{enricher}"],
        )

    # ── clear ─────────────────────────────────────────────────────────

    def _clear(self, db, campaign, campaign_id, CampaignContact) -> AgentResult:
        if campaign.status not in ("draft", "audience_built"):
            return AgentResult(
                status="VALIDATION_ERROR",
                error="Can only clear audience from draft/audience_built campaigns",
            )
        deleted = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id
        ).delete()
        campaign.status = "draft"
        db.commit()
        return AgentResult(
            status="SUCCESS",
            output={"campaign_id": campaign_id, "contacts_removed": deleted},
            actions_taken=["audience_cleared"],
        )

    # ── report ────────────────────────────────────────────────────────

    def _report(self, db, campaign, campaign_id, CampaignContact) -> AgentResult:
        contacts = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id
        ).all()
        by_type: dict[str, int] = {}
        by_source: dict[str, int] = {}
        has_phone = has_email = 0
        for c in contacts:
            by_type[c.contact_type] = by_type.get(c.contact_type, 0) + 1
            # Infer source from contact_ref prefix
            prefix = c.contact_ref.split("_")[0] if "_" in c.contact_ref else "unknown"
            by_source[prefix] = by_source.get(prefix, 0) + 1
            if c.msisdn:
                has_phone += 1
            if c.email:
                has_email += 1

        total = len(contacts)
        return AgentResult(
            status="SUCCESS",
            output={
                "campaign_id": campaign_id,
                "campaign_name": campaign.name,
                "status": campaign.status,
                "total_contacts": total,
                "by_type": by_type,
                "by_source": by_source,
                "has_phone": has_phone,
                "has_email": has_email,
                "missing_phone": total - has_phone,
                "missing_email": total - has_email,
                "messages_sent": sum(1 for c in contacts if c.message_sent_ts),
                "responses": sum(1 for c in contacts if c.response_ts),
                "hint": "Run 'enrich' action to fill missing phone/email via RocketReach." if (total - has_phone + total - has_email) > 0 else None,
            },
            actions_taken=["audience_report_generated"],
        )
