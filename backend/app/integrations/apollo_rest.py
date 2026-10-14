"""Apollo.io REST client — headless backend use (API key, not OAuth).

Used by K2 Audience Builder for prospecting new dealers.
This is distinct from the MCP client (apollo_mcp_client.py) which uses OAuth
for conversational use through Claude Code.

REST docs: https://apolloio.github.io/apollo-api-docs/
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

BASE = "https://api.apollo.io/v1"


class ApolloRestError(Exception):
    pass


def _require_key() -> str:
    key = settings.apollo_api_key
    if not key:
        raise ApolloRestError("APOLLO_API_KEY not set in .env")
    return key


def _simplify_person(p: dict) -> dict:
    """Extract the fields K2 cares about from an Apollo person record."""
    # Apollo nests phone under phone_numbers list
    phone = None
    for ph in p.get("phone_numbers", []):
        if isinstance(ph, dict) and ph.get("sanitized_number"):
            phone = ph["sanitized_number"]
            break
    # Name
    name = p.get("name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    # Location
    parts = [p.get("city"), p.get("state"), p.get("country")]
    location = ", ".join(x for x in parts if x)
    return {
        "apollo_id": p.get("id", ""),
        "name": name,
        "email": p.get("email"),
        "phone": phone,
        "linkedin_url": p.get("linkedin_url"),
        "title": p.get("title"),
        "company": (p.get("organization") or {}).get("name") or p.get("organization_name", ""),
        "location": location,
    }


def search_people(
    keywords: str | None = None,
    location: str | None = None,
    title: str | None = None,
    industry: str | None = None,
    per_page: int = 25,
    page: int = 1,
) -> list[dict]:
    """Search for people via Apollo mixed_people/search.

    Returns simplified person dicts. Returns [] when unconfigured or on error.
    """
    if not settings.apollo_api_key:
        logger.debug("Apollo REST not configured — skipping search")
        return []

    body: dict = {
        "api_key": settings.apollo_api_key,
        "per_page": min(per_page, 100),
        "page": page,
    }
    if keywords:
        body["q_keywords"] = keywords
    if location:
        body["person_locations"] = [location]
    if title:
        body["person_titles"] = [title]
    if industry:
        body["organization_industry_tag_ids"] = [industry]

    try:
        resp = httpx.post(
            f"{BASE}/mixed_people/search",
            json=body,
            headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
            timeout=20,
        )
        if resp.status_code == 401:
            logger.warning("Apollo REST 401 — check APOLLO_API_KEY")
            return []
        if resp.status_code == 429:
            logger.warning("Apollo REST rate limited")
            return []
        resp.raise_for_status()
        data = resp.json()
        people = data.get("people") or data.get("contacts") or []
        return [_simplify_person(p) for p in people]
    except ApolloRestError:
        raise
    except Exception as exc:
        logger.warning("Apollo search_people error: %s", exc)
        return []


def match_person(name: str, company: str) -> dict | None:
    """Match a specific person by name + company.

    Returns simplified dict or None if not found / unconfigured.
    """
    if not settings.apollo_api_key:
        return None
    try:
        resp = httpx.post(
            f"{BASE}/people/match",
            json={
                "api_key": settings.apollo_api_key,
                "name": name,
                "organization_name": company,
                "reveal_personal_emails": False,
            },
            headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
            timeout=15,
        )
        if resp.status_code in (404, 422):
            return None
        if resp.status_code == 401:
            logger.warning("Apollo REST 401 — check APOLLO_API_KEY")
            return None
        resp.raise_for_status()
        person = resp.json().get("person")
        if not person:
            return None
        return _simplify_person(person)
    except Exception as exc:
        logger.warning("Apollo match_person error for %s @ %s: %s", name, company, exc)
        return None
