"""Dynamic email context — pulls live data from every connected source at
send time, for use in K3 campaign and transactional templates.

Sources, each best-effort and independent:
  local_mart   — DealerMart (this database's own nightly Books+CRM+F1+S2
                 join, keyed by contact_ref == dealer_ref). Free — no
                 network call, just a row lookup in the current session.
  cloud_db     — the cloud replica of the same marts (mcp_servers/cloud_db),
                 used only as a fallback when local_mart has no row for
                 this contact (e.g. a satellite deployment).
  zoho_books   — live Zoho Books contact + statement balance.
  zoho_crm     — live Zoho CRM contact/account match.
  apollo       — Apollo.io person enrichment.
  rocketreach  — RocketReach person enrichment.

If a source is unconfigured, unreachable, or the contact isn't found
there, that source simply contributes no fields — it never fails the
send. Fields are namespaced by source prefix (mart_, books_, crm_,
apollo_, rocketreach_) so nothing collides, and a template that doesn't
reference a prefix never triggers that source's call in the first place
(see referenced_sources()) — a plain static campaign costs nothing extra
per recipient.
"""

import logging

logger = logging.getLogger(__name__)

_SOURCE_PREFIXES = {
    "mart_": "local_mart",
    "books_": "zoho_books",
    "crm_": "zoho_crm",
    "apollo_": "apollo",
    "rocketreach_": "rocketreach",
}


def referenced_sources(body: str) -> set[str]:
    """Which dynamic sources this template body actually references, by
    scanning for each source's field prefix. Used to skip a source's
    network call entirely when nothing in the template needs it."""
    body = body or ""
    return {source for prefix, source in _SOURCE_PREFIXES.items() if prefix in body}


def _safe(source_name: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.debug("dynamic_email: %s source unavailable", source_name, exc_info=True)
        return None


def _from_local_mart(db, contact_ref: str) -> dict:
    from app.models.enterprise import DealerMart
    row = db.get(DealerMart, contact_ref)
    if row is None:
        return {}
    return {
        "mart_outstanding_amt": row.outstanding_amt,
        "mart_payment_behavior_score": row.payment_behavior_score,
        "mart_last_order_date": row.last_order_ts.date().isoformat() if row.last_order_ts else None,
        "mart_has_scheme_eligibility": bool(row.scheme_eligibility_json),
        "mart_scheme_eligibility": row.scheme_eligibility_json or {},
        "mart_stock_opportunity": row.stock_opportunity_json or {},
        "mart_branch": row.branch,
    }


def _from_cloud_mart(contact_ref: str) -> dict:
    from mcp_servers.cloud_db import server as cloud_server
    row = cloud_server.dealer_get(contact_ref)
    if not row:
        return {}
    return {
        "mart_outstanding_amt": row.get("outstanding_amt"),
        "mart_last_order_date": row.get("last_order_ts"),
        "mart_branch": row.get("branch"),
    }


def _from_zoho_books(email_addr: str | None) -> dict:
    if not email_addr:
        return {}
    from app.integrations.zoho_books import get_zoho_client
    client = get_zoho_client()
    if not client.configured:
        return {}
    matches = client.search_contacts(email_addr)
    if not matches:
        return {}
    contact = matches[0]
    out = {
        "books_contact_name": contact.get("contact_name"),
        "books_outstanding_receivable": contact.get("outstanding_receivable_amount"),
    }
    contact_id = contact.get("contact_id")
    if contact_id:
        stmt = _safe("zoho_books.statement", client.get_contact_statements, contact_id)
        if stmt:
            out["books_statement_balance"] = (stmt.get("statement") or {}).get("balance")
    return out


def _from_zoho_crm(email_addr: str | None) -> dict:
    if not email_addr:
        return {}
    from app.integrations.zoho_crm import get_zoho_crm_client
    client = get_zoho_crm_client()
    if not client.configured:
        return {}
    contacts = client.list_contacts(per_page=200)
    match = next(
        (c for c in contacts if (c.get("Email") or "").lower() == email_addr.lower()),
        None,
    )
    if not match:
        return {}
    account = match.get("Account_Name")
    account_name = account.get("name") if isinstance(account, dict) else account
    return {
        "crm_contact_id": match.get("id"),
        "crm_account_name": account_name,
        "crm_lead_source": match.get("Lead_Source"),
    }


def _from_apollo(name: str | None, company: str | None) -> dict:
    if not name:
        return {}
    from app.integrations.apollo_rest import match_person
    person = match_person(name, company or "")
    if not person:
        return {}
    return {
        "apollo_title": person.get("title"),
        "apollo_company": person.get("organization_name") or person.get("company"),
        "apollo_linkedin_url": person.get("linkedin_url"),
    }


def _from_rocketreach(name: str | None, company: str | None) -> dict:
    if not name:
        return {}
    from app.integrations.rocketreach_client import lookup_person
    person = lookup_person(name, company or "")
    if not person:
        return {}
    return {
        "rocketreach_verified_email": person.get("email"),
        "rocketreach_phone": person.get("phone"),
    }


def build_live_context(
    db,
    contact_ref: str,
    email_addr: str | None,
    display_name: str | None = None,
    company: str | None = None,
    sources: set[str] | None = None,
) -> dict:
    """Aggregate live context from the requested sources (default: all).
    Every source is independent and best-effort — see module docstring."""
    sources = sources if sources is not None else set(_SOURCE_PREFIXES.values())
    ctx: dict = {}

    if "local_mart" in sources:
        mart = _safe("local_mart", _from_local_mart, db, contact_ref) or {}
        if not mart:
            mart = _safe("cloud_db", _from_cloud_mart, contact_ref) or {}
        ctx.update(mart)

    if "zoho_books" in sources:
        ctx.update(_safe("zoho_books", _from_zoho_books, email_addr) or {})
    if "zoho_crm" in sources:
        ctx.update(_safe("zoho_crm", _from_zoho_crm, email_addr) or {})
    if "apollo" in sources:
        ctx.update(_safe("apollo", _from_apollo, display_name, company) or {})
    if "rocketreach" in sources:
        ctx.update(_safe("rocketreach", _from_rocketreach, display_name, company) or {})

    return ctx
