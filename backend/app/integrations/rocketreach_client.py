"""RocketReach — REAL REST (no MCP, no mock).

Live API: https://api.rocketreach.co/v2/api/*
Auth: Header Api-Key: <ROCKETREACH_API_KEY>  (Ultimate tier for bulk)

Yexis uses this for H1 Talent Sourcing (Apollo+RR) and non-tech coverage,
and for K2 campaign audience enrichment (fill missing phone/email on contacts).
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

BASE = "https://api.rocketreach.co/v2/api"


class RocketReachError(Exception):
    pass


def _headers() -> dict:
    key = settings.rocketreach_api_key
    if not key:
        raise RocketReachError("ROCKETREACH_API_KEY not set in .env")
    return {"Api-Key": key, "Content-Type": "application/json"}


def health() -> dict:
    key = settings.rocketreach_api_key
    if not key:
        return {"configured": False, "hint": "Set ROCKETREACH_API_KEY in backend/.env (Ultimate tier). Get from https://rocketreach.co/api"}
    try:
        r = httpx.get(f"{BASE}/search", params={"page_size": 1}, headers={"Api-Key": key}, timeout=10)
        if r.status_code == 401:
            return {"configured": True, "live": False, "error": "401 Unauthorized — invalid API key"}
        if r.status_code == 402:
            return {"configured": True, "live": False, "error": "402 Payment Required — quota exhausted or plan limit"}
        r.raise_for_status()
        return {"configured": True, "live": True, "status": r.status_code, "sample": r.json().get("profiles", [])[:1]}
    except httpx.HTTPStatusError as e:
        return {"configured": True, "live": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:400]}"}
    except Exception as e:
        return {"configured": True, "live": False, "error": str(e)}


def _extract_best_email(profile: dict) -> str | None:
    """Pick the first verified email from the profile's emails list."""
    emails = profile.get("emails") or []
    # Prefer verified, then any
    for email_obj in emails:
        if isinstance(email_obj, dict) and email_obj.get("is_verified"):
            return email_obj.get("email")
    for email_obj in emails:
        if isinstance(email_obj, dict):
            addr = email_obj.get("email")
            if addr:
                return addr
        if isinstance(email_obj, str) and "@" in email_obj:
            return email_obj
    # Flat field fallback
    return profile.get("email") or profile.get("work_email") or None


def _extract_best_phone(profile: dict) -> str | None:
    """Pick the first mobile or direct phone from the profile's phones list."""
    phones = profile.get("phones") or []
    # Priority: mobile > direct > any
    for ptype in ("mobile", "direct", "work", "other"):
        for ph in phones:
            if isinstance(ph, dict):
                if ph.get("type", "").lower() == ptype and ph.get("number"):
                    return ph["number"]
    for ph in phones:
        if isinstance(ph, dict) and ph.get("number"):
            return ph["number"]
        if isinstance(ph, str) and ph:
            return ph
    return profile.get("phone") or None


def lookup_person(name: str, company: str) -> dict | None:
    """Look up a single person by name + company.

    Returns a simplified dict with name, email, phone, linkedin_url, title.
    Returns None if not found or not configured.
    """
    if not settings.rocketreach_api_key:
        logger.debug("RocketReach not configured — skipping lookup for %s", name)
        return None
    try:
        r = httpx.post(
            f"{BASE}/lookupProfile",
            json={"name": name, "current_employer": company},
            headers=_headers(),
            timeout=15,
        )
        if r.status_code == 404:
            return None
        if r.status_code == 402:
            logger.warning("RocketReach quota exhausted")
            return None
        r.raise_for_status()
        profile = r.json()
        if not profile or profile.get("status") == "failed":
            return None
        return {
            "name": profile.get("name") or name,
            "email": _extract_best_email(profile),
            "phone": _extract_best_phone(profile),
            "linkedin_url": profile.get("linkedin_url"),
            "title": profile.get("current_title"),
            "company": profile.get("current_employer") or company,
        }
    except httpx.HTTPStatusError as exc:
        logger.warning("RocketReach lookup failed for %s: HTTP %s", name, exc.response.status_code)
        return None
    except Exception as exc:
        logger.warning("RocketReach lookup error for %s: %s", name, exc)
        return None


def search_people(
    name: str | None = None,
    company: str | None = None,
    location: str | None = None,
    page_size: int = 10,
) -> list[dict]:
    """Search for people matching the given criteria.

    Returns a list of simplified profile dicts.
    Returns empty list if not configured or on error.
    """
    if not settings.rocketreach_api_key:
        logger.debug("RocketReach not configured — skipping search")
        return []
    # RocketReach v2 /search is a POST with a JSON `query` object whose values
    # are arrays; a GET returns 405 Method Not Allowed.
    query: dict = {}
    if name:
        query["name"] = [name]
    if company:
        query["current_employer"] = [company]
    if location:
        query["location"] = [location]
    try:
        r = httpx.post(
            f"{BASE}/search",
            json={"query": query, "page_size": min(page_size, 25)},
            headers=_headers(),
            timeout=15,
        )
        if r.status_code == 402:
            logger.warning("RocketReach quota exhausted")
            return []
        r.raise_for_status()
        profiles = r.json().get("profiles", [])
        return [
            {
                "name": p.get("name", ""),
                "email": _extract_best_email(p),
                "phone": _extract_best_phone(p),
                "linkedin_url": p.get("linkedin_url"),
                "title": p.get("current_title"),
                "company": p.get("current_employer", company or ""),
                "location": p.get("location"),
            }
            for p in profiles
        ]
    except Exception as exc:
        logger.warning("RocketReach search error: %s", exc)
        return []
