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
VALID_SOURCES = {"dealermart", "zoho_books", "zoho_crm", "apollo", "rocketreach", "excel", "crm_accounts", "justdial", "scribd", "google_drive", "all"}
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


def _unwrap_mcp_records(resp: dict, collection: str) -> tuple[list[dict], dict]:
    """Extract a record page and pagination metadata from an MCP tool result."""
    import json as _json

    result = resp.get("result", resp)
    if not isinstance(result, dict):
        raise RuntimeError("Unexpected Zoho MCP result")
    if result.get("isError"):
        content = result.get("content") or []
        detail = content[0].get("text", "MCP tool call failed") if content else "MCP tool call failed"
        raise RuntimeError(detail)

    payload = result.get("structuredContent")
    if not isinstance(payload, dict):
        content = result.get("content")
        if isinstance(content, list) and content and content[0].get("text"):
            try:
                payload = _json.loads(content[0]["text"])
            except (ValueError, TypeError) as exc:
                raise RuntimeError("Could not parse Zoho MCP tool response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unrecognised Zoho MCP response shape: keys={list(result.keys())}")

    payload = payload.get("data", payload)
    if isinstance(payload, list):
        return payload, {}
    if not isinstance(payload, dict):
        raise RuntimeError("Zoho MCP response did not contain a record list")

    rows = payload.get("data")
    if rows is None:
        rows = payload.get(collection) or []
    if not isinstance(rows, list):
        raise RuntimeError("Zoho MCP response record field was not a list")
    return rows, payload.get("info") or payload.get("page_context") or {}


def _mcp_list_records(
    server: str,
    tool_name: str,
    collection: str,
    query_params: dict,
    page_size: int = 200,
) -> list[dict]:
    """Read all pages from a named Zoho MCP list tool."""
    from app.integrations.zoho_mcp_client import get_zoho_mcp_client

    client = get_zoho_mcp_client(server)
    tools = client.list_tools()
    if not any(tool.get("name") == tool_name for tool in tools):
        raise RuntimeError(f"{server} Zoho MCP server does not expose {tool_name}")

    rows: list[dict] = []
    page = 1
    page_token = None
    while len(rows) < 50_000:
        params = {**query_params, "per_page": page_size}
        if page_token:
            params["page_token"] = page_token
        else:
            params["page"] = page
        response = client.call_tool(tool_name, {"query_params": params})
        batch, info = _unwrap_mcp_records(response, collection)
        if not batch:
            break
        rows.extend(batch)
        if not (info.get("more_records") or info.get("has_more_page")):
            break
        page_token = info.get("next_page_token")
        if not page_token:
            page += 1
    return rows[:50_000]


def _mcp_fetch_books_customers() -> list[dict]:
    """Pull customer contacts from the Zoho Books MCP server."""
    from app.core.config import settings

    organization_id = settings.zoho_org_id
    if not organization_id:
        raise RuntimeError("ZOHO_ORG_ID is required for Zoho Books MCP customer listing")
    return _mcp_list_records(
        "YEXIS",
        "ZohoBooks_list_contacts",
        "contacts",
        {"organization_id": organization_id, "contact_type": "customer"},
    )


def _fetch_zoho_books(page_limit: int = 5) -> tuple[list[dict], str | None]:
    """Pull Zoho Books customers through MCP first, then fall back to REST."""
    try:
        from app.integrations.zoho_books import get_zoho_client
        client = get_zoho_client()
        raw = None
        try:
            raw = _mcp_fetch_books_customers()
            logger.info("Zoho Books customers via MCP: %d records", len(raw))
        except Exception as exc:
            logger.warning("Zoho Books MCP fetch failed, falling back to REST: %s", exc)
        if raw is None:
            if not client.configured:
                return [], "Zoho Books not configured (MCP customer listing unavailable)"
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


def _mcp_fetch_accounts() -> list[dict]:
    """Pull every CRM Account via the Zoho MCP server (ZohoCRM_getAccountsRecords),
    paginating with page/per_page and Zoho's page_token for results beyond 2000.

    The MCP response is wrapped in JSON-RPC; the CRM payload surfaces either as
    structuredContent.data or as JSON text in content[0].text. Raises on any
    failure so the caller can fall back to REST.
    """
    fields = "Account_Name,Phone,Email,Billing_City,Billing_State,Website"
    return _mcp_list_records(
        "AGENT",
        "ZohoCRM_getAccountsRecords",
        "accounts",
        {"fields": fields},
    )


def _mcp_fetch_contacts() -> list[dict]:
    """Pull CRM Contacts via MCP when the server exposes a list tool."""
    fields = "Full_Name,First_Name,Last_Name,Phone,Mobile,Email,Account_Name"
    return _mcp_list_records(
        "AGENT",
        "ZohoCRM_getContactsRecords",
        "contacts",
        {"fields": fields},
    )


def _fetch_zoho_crm() -> tuple[list[dict], str | None]:
    """Pull accounts + contacts from Zoho CRM.

    MCP is tried first for both record types, with the REST client used when a
    MCP tool is unavailable or fails.
    """
    try:
        from app.core.config import settings
        from app.integrations.zoho_crm import get_zoho_crm_client, ZohoCrmError
        client = get_zoho_crm_client()
        contacts = []
        errors = []

        accounts = None
        if settings.k2_zoho_mcp_enabled:
            try:
                accounts = _mcp_fetch_accounts()
                logger.info("CRM accounts via MCP: %d records", len(accounts))
            except Exception as exc:
                logger.warning("MCP accounts fetch failed, falling back to REST: %s", exc)
                accounts = None
        try:
            if accounts is None:
                if not client.configured:
                    raise ZohoCrmError(0, "Zoho CRM REST fallback is not configured")
                accounts = client.list_all_accounts()
            for a in accounts:
                aid = a.get("id") or a.get("Account_Name", "")
                contacts.append({
                    "contact_ref": f"crm_{aid}",
                    "msisdn": _normalise_phone(a.get("Phone")),
                    "email": a.get("Email"),
                })
        except Exception as exc:
            errors.append(f"accounts: {exc}")
            logger.warning("CRM accounts fetch failed: %s", exc)

        people = None
        if settings.k2_zoho_mcp_enabled:
            try:
                people = _mcp_fetch_contacts()
                logger.info("CRM contacts via MCP: %d records", len(people))
            except Exception as exc:
                logger.warning("MCP contacts fetch failed, falling back to REST: %s", exc)
        try:
            if people is None:
                if not client.configured:
                    raise ZohoCrmError(0, "Zoho CRM REST fallback is not configured")
                people = client.list_all_contacts()
            for p in people:
                pid = p.get("id") or ""
                contacts.append({
                    "contact_ref": f"crm_contact_{pid}",
                    "msisdn": _normalise_phone(p.get("Mobile") or p.get("Phone")),
                    "email": p.get("Email"),
                })
        except Exception as exc:
            errors.append(f"contacts: {exc}")
            logger.warning("CRM contacts fetch failed: %s", exc)

        return contacts, ("; ".join(errors) if errors and not contacts else None)
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


def _fetch_rocketreach(
    name: str | None,
    company: str | None,
    location: str | None,
    per_page: int,
) -> tuple[list[dict], str | None]:
    """Search new prospects via RocketReach.

    Mirrors the Apollo source: each matched profile becomes one contact,
    carrying title/location/linkedin into extra_json for template use.
    """
    try:
        from app.core.config import settings
        from app.integrations import rocketreach_client as rr
        if not settings.rocketreach_api_key:
            return [], "RocketReach not configured (set ROCKETREACH_API_KEY)"
        people = rr.search_people(
            name=name, company=company, location=location, page_size=per_page,
        )
        contacts = []
        for p in people:
            key = p.get("email") or p.get("linkedin_url") or p.get("name") or "unknown"
            ref_safe = re.sub(r"[^a-zA-Z0-9_@.\-]", "_", str(key))
            extra = {
                k: v for k, v in (
                    ("title", p.get("title")),
                    ("location", p.get("location")),
                    ("linkedin_url", p.get("linkedin_url")),
                ) if v
            }
            contacts.append({
                "contact_ref": f"rocketreach_{ref_safe}",
                "msisdn": _normalise_phone(p.get("phone")),
                "email": p.get("email"),
                "name": p.get("name") or None,
                "company": p.get("company") or None,
                "contact_type": "lead",
                "extra": extra or None,
            })
        return contacts, None
    except Exception as exc:
        logger.warning("RocketReach source error: %s", exc)
        return [], str(exc)


def _fetch_justdial(payload: dict) -> tuple[list[dict], str | None]:
    """Search JustDial's official partner API for business contacts.

    Filters: keywords, location/city, category/industry, per_page. Each listing
    with a usable phone or email becomes one contact; city/category ride along
    in extra_json for template use.
    """
    try:
        from app.integrations import justdial_client as jd
        businesses = jd.search_businesses(
            keywords=payload.get("keywords"),
            city=payload.get("location") or payload.get("city"),
            category=payload.get("category") or payload.get("industry"),
            limit=int(payload.get("per_page", 50)),
        )
    except Exception as exc:
        logger.warning("JustDial source error: %s", exc)
        return [], str(exc)

    contacts = []
    for b in businesses:
        phone = _normalise_phone(b.get("phone"))
        email = (b.get("email") or "").strip().lower() or None
        if not phone and not email:
            continue
        key = email or phone
        ref_safe = re.sub(r"[^a-zA-Z0-9_@.\-]", "_", str(key))
        extra = {
            k: v for k, v in (("city", b.get("city")), ("category", b.get("category"))) if v
        }
        contacts.append({
            "contact_ref": f"justdial_{ref_safe}",
            "msisdn": phone,
            "email": email,
            "name": b.get("name") or None,
            "company": b.get("company") or None,
            "contact_type": "lead",
            "extra": extra or None,
        })
    return contacts, None


def _extract_pdf_contacts(file_path: str, ref_prefix: str = "doc") -> tuple[list[dict], str | None]:
    """Heuristically pull contacts from a PDF's text layer. Each line holding an
    email and/or a phone becomes one contact; the line's alphabetic words
    (minus the email) are used as a best-guess name. Scanned/image-only PDFs
    have no text layer and yield nothing."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return [], "pypdf not installed — cannot parse PDF contact documents"
    try:
        reader = PdfReader(file_path)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        return [], f"Could not read PDF: {exc}"

    email_re = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    contacts: list[dict] = []
    seen: set[str] = set()
    for line in text.splitlines():
        emails = email_re.findall(line)
        email = emails[0].lower() if emails else None
        phone = None
        for run in re.findall(r"\+?\d[\d\-\s]{8,}\d", line):
            p = _normalise_phone(run)
            if p:
                phone = p
                break
        if not email and not phone:
            continue
        ref_base = email or phone
        if ref_base in seen:
            continue
        seen.add(ref_base)
        name_guess = " ".join(
            w for w in email_re.sub("", line).split() if any(ch.isalpha() for ch in w)
        )[:120].strip() or None
        ref_safe = re.sub(r"[^a-zA-Z0-9_@.\-]", "_", ref_base)
        contacts.append({
            "contact_ref": f"{ref_prefix}_{ref_safe}",
            "msisdn": phone,
            "email": email,
            "name": name_guess,
            "contact_type": "lead",
        })
    return contacts, (None if contacts else "No emails or phone numbers found in the PDF text layer")


def _suffix_from_name(name: str | None) -> str:
    low = (name or "").lower()
    if low.endswith(".csv"):
        return ".csv"
    if low.endswith(".xls"):
        return ".xls"
    return ".xlsx"


def _fetch_scribd(payload: dict) -> tuple[list[dict], str | None]:
    """Download a contact document from Scribd (or any reachable URL) and parse
    it. PDFs go through the text extractor; Excel/CSV through the normal
    spreadsheet parser."""
    import os as _os
    import tempfile

    url = payload.get("document_url") or payload.get("url")
    if not url:
        return [], "source=scribd needs a 'document_url'"
    try:
        from app.integrations import scribd_client as sc
        content, suffix = sc.fetch_document(url)
    except Exception as exc:
        logger.warning("Scribd source error: %s", exc)
        return [], str(exc)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(content)
        tmp.close()
        if suffix == ".pdf":
            return _extract_pdf_contacts(tmp.name, ref_prefix="scribd")
        return _fetch_excel(tmp.name)
    finally:
        _os.unlink(tmp.name)


def _drive_collect_and_parse(
    payload: dict, *, resolve_link, list_files, download_file,
) -> tuple[list[dict], str | None]:
    """Shared Drive import logic. Resolves the set of file ids to import
    (file_id / drive_link / folder_id, or the default folder), downloads each
    spreadsheet, and parses it through the normal spreadsheet path. The three
    callables abstract over the transport, so the service-account REST client
    and the MCP client share one implementation."""
    import os as _os
    import tempfile

    errors: list[str] = []
    file_ids: list[str] = []

    link = payload.get("drive_link") or payload.get("drive_url")
    fid = payload.get("file_id") or (resolve_link(link) if link else None)
    if fid:
        file_ids.append(fid)
    elif link:
        errors.append(f"Could not extract a Drive file id from link: {link}")

    folder = payload.get("folder_id")
    if folder or not file_ids:
        try:
            file_ids.extend(f["id"] for f in list_files(folder))
        except Exception as exc:
            errors.append(str(exc))

    # De-dupe while preserving order.
    file_ids = list(dict.fromkeys(file_ids))
    if not file_ids:
        return [], "; ".join(errors) or (
            "No Drive spreadsheet specified — provide file_id, drive_link, or folder_id"
        )

    all_contacts: list[dict] = []
    for fid in file_ids:
        try:
            content, name = download_file(fid)
        except Exception as exc:
            errors.append(f"{fid}: {exc}")
            continue
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=_suffix_from_name(name))
        try:
            tmp.write(content)
            tmp.close()
            rows, err = _fetch_excel(tmp.name)
            if err and not rows:
                errors.append(f"{name}: {err}")
            all_contacts.extend(rows)
        finally:
            _os.unlink(tmp.name)

    return all_contacts, ("; ".join(errors) if errors else None)


def _fetch_google_drive_mcp(payload: dict) -> tuple[list[dict], str | None]:
    """Google Drive import via an MCP server (Streamable HTTP). Mirrors the REST
    path but routes file discovery and download through google_drive_mcp_client.
    The link->id parser is reused from the REST client."""
    try:
        from app.integrations import google_drive_mcp_client as gd_mcp
        from app.integrations.google_drive_client import resolve_link
        client = gd_mcp.get_client()
    except Exception as exc:
        logger.warning("Google Drive MCP source error: %s", exc)
        return [], str(exc)

    return _drive_collect_and_parse(
        payload,
        resolve_link=resolve_link,
        list_files=lambda folder: client.search_files(folder_id=folder),
        download_file=client.download_file,
    )


def _fetch_google_drive(payload: dict) -> tuple[list[dict], str | None]:
    """Pull contact spreadsheets from Google Drive. Supports three modes:
      - a single file_id
      - a shared drive_link (file id extracted from the URL)
      - a folder_id (every spreadsheet in the folder is imported)
    With none given, the default folder (settings.google_drive_folder_id) is used.
    Google Sheets are exported to .xlsx; all files run through the spreadsheet parser.

    Transport: when settings.google_drive_mcp_server_url is set, the Drive MCP
    server is tried first; if it yields nothing usable (e.g. a server that needs
    interactive OAuth returns 401) the service-account REST client takes over.
    Otherwise the REST client is used directly."""
    from app.core.config import settings

    if settings.google_drive_mcp_server_url:
        rows, err = _fetch_google_drive_mcp(payload)
        # Fall back only when MCP produced nothing usable — a successful MCP
        # import with per-file errors is returned as-is.
        if rows or not err:
            return rows, err
        logger.warning("Drive MCP returned nothing, falling back to REST client: %s", err)

    try:
        from app.integrations import google_drive_client as gd
    except Exception as exc:
        return [], str(exc)

    return _drive_collect_and_parse(
        payload,
        resolve_link=gd.resolve_link,
        list_files=gd.list_spreadsheets,
        download_file=gd.download_file,
    )


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
_STD_TARGETS = {"name", "first_name", "salutation", "email", "phone", "company", "ignore"}

_NAME_COLS    = {
    "name", "contact_name", "full_name", "contact", "contact_person",
    "person_name", "client_name", "customer_name",
}
_FIRST_COLS   = {"first_name", "firstname", "first", "given_name"}
_SALUTATION_COLS = {"salutation", "honorific", "title_prefix", "prefix"}
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
    if h in _SALUTATION_COLS:
        return "salutation"
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
    salutation_cols = [i for i, t in resolved.items() if t == "salutation"]
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
        salutation = _first(salutation_cols)
        if salutation:
            extra["salutation"] = salutation
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
            if src == "scribd" and not (payload.get("document_url") or payload.get("url")):
                return "'document_url' is required when source is 'scribd'"
            if src == "google_drive":
                from app.core.config import settings
                if not (
                    payload.get("file_id")
                    or payload.get("drive_link")
                    or payload.get("drive_url")
                    or payload.get("folder_id")
                    or settings.google_drive_folder_id
                ):
                    return "google_drive needs a 'file_id', 'drive_link', or 'folder_id'"
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
            elif src == "rocketreach":
                rows, err = _fetch_rocketreach(
                    payload.get("name"), payload.get("company") or keywords, location, per_page,
                )
            elif src == "excel":
                rows, err = _fetch_excel(file_path or "")
            elif src == "justdial":
                rows, err = _fetch_justdial(payload)
            elif src == "scribd":
                rows, err = _fetch_scribd(payload)
            elif src == "google_drive":
                rows, err = _fetch_google_drive(payload)
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
