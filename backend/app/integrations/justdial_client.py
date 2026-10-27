"""JustDial — official partner API source for K2 audience building.

JustDial exposes listing / lead data through its partner API (JD Omni / Leads
API). The exact endpoint path, auth style and field names depend on your
JustDial contract, so they are driven by settings and the response mapping
lives in one place (`_map_record`) for you to align with your API docs.

Config (backend/.env):
  JUSTDIAL_API_URL     base URL of your JustDial API (e.g. https://api.justdial.com)
  JUSTDIAL_API_KEY     API key — sent as both a Bearer header and an api_key
                       query param to cover the two common auth styles
  JUSTDIAL_API_SECRET  optional partner secret/token (sent as X-JD-Secret)

Once your credentials are in place, adjust `SEARCH_PATH` and `_map_record`
to match your contract, then build an audience with source=justdial.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Adjust to your JustDial API's search/listing path.
SEARCH_PATH = "/search"


class JustDialError(Exception):
    pass


def _configured() -> bool:
    return bool(settings.justdial_api_key and settings.justdial_api_url)


def _map_record(rec: dict) -> dict:
    """Map one JustDial record to the common contact shape.

    ADJUST these field names to match your JustDial API response payload.
    """
    name = rec.get("name") or rec.get("company") or rec.get("docname")
    phone = (
        rec.get("mobile")
        or rec.get("phone")
        or rec.get("mobileno")
        or rec.get("contact")
    )
    email = rec.get("email") or rec.get("emailid")
    return {
        "name": name,
        "company": rec.get("company") or rec.get("docname") or name,
        "phone": phone,
        "email": email,
        "city": rec.get("city") or rec.get("area"),
        "category": rec.get("category") or rec.get("catname"),
    }


def search_businesses(
    keywords: str | None = None,
    city: str | None = None,
    category: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Search JustDial listings and return records in the common contact shape."""
    if not _configured():
        raise JustDialError(
            "JustDial not configured — set JUSTDIAL_API_URL and JUSTDIAL_API_KEY in backend/.env"
        )

    params = {
        "q": keywords or category or "",
        "city": city or "",
        "category": category or "",
        "limit": limit,
        "api_key": settings.justdial_api_key,
    }
    headers = {
        "Authorization": f"Bearer {settings.justdial_api_key}",
        "Accept": "application/json",
    }
    if settings.justdial_api_secret:
        headers["X-JD-Secret"] = settings.justdial_api_secret

    url = settings.justdial_api_url.rstrip("/") + SEARCH_PATH
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as exc:
        raise JustDialError(
            f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
        ) from exc
    except Exception as exc:
        raise JustDialError(str(exc)) from exc

    if isinstance(data, list):
        records = data
    else:
        records = (
            data.get("results") or data.get("data") or data.get("docs") or []
        )
    return [_map_record(rec) for rec in records if isinstance(rec, dict)]


def health() -> dict:
    if not _configured():
        return {
            "configured": False,
            "hint": "Set JUSTDIAL_API_URL + JUSTDIAL_API_KEY in backend/.env",
        }
    return {
        "configured": True,
        "hint": (
            f"Confirm SEARCH_PATH ('{SEARCH_PATH}') and _map_record() match your "
            "JustDial contract, then build an audience with source=justdial"
        ),
    }
