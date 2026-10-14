"""Apollo.io MCP — REAL (live REST + MCP endpoint).

Two modes:
  1. Direct REST (Api-Key): https://api.apollo.io/v1/* — used by H1 Sourcing agent
  2. MCP endpoint (OAuth): https://mcp.apollo.io/mcp — used by Claude/Cowork sessions

This server implements (1) for headless lanes with real Api-Key.
For (2) add to .mcp.json: {"apollo":{"url":"https://mcp.apollo.io/mcp"}} and OAuth via Claude Code.

Tools: people_search, organization_search, enrich_person, create_contact, add_to_sequence
"""

import httpx
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)
BASE = "https://api.apollo.io/v1"

class ApolloError(Exception):
    pass

def _require():
    if not settings.apollo_api_key:
        raise ApolloError("APOLLO_API_KEY not set in backend/.env — Apollo Organization tier required for programmatic search/enrichment.")

def _headers():
    _require()
    return {"Content-Type": "application/json", "Cache-Control": "no-cache", "X-Api-Key": settings.apollo_api_key}

def people_search(q_organization_name: str | None = None, person_titles: list[str] | None = None, page: int = 1, per_page: int = 25) -> dict:
    _require()
    payload = {"page": page, "per_page": min(per_page, 100)}
    if q_organization_name:
        payload["q_organization_name"] = q_organization_name
    if person_titles:
        payload["person_titles"] = person_titles
    resp = httpx.post(f"{BASE}/mixed_people/search", json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    logger.info("Apollo people_search -> %d people", len(data.get("people", [])))
    return data

def organization_search(q_organization_name: str, page: int = 1) -> dict:
    _require()
    payload = {"q_organization_name": q_organization_name, "page": page}
    resp = httpx.post(f"{BASE}/mixed_companies/search", json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()

def enrich_person(email: str | None = None, linkedin_url: str | None = None) -> dict:
    _require()
    payload = {}
    if email:
        payload["email"] = email
    if linkedin_url:
        payload["linkedin_url"] = linkedin_url
    if not payload:
        raise ApolloError("enrich_person requires email or linkedin_url")
    resp = httpx.post(f"{BASE}/people/match", json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()

def list_tools():
    return [
        {"name": "people_search", "description": "Live Apollo people search (headless lane)", "input_schema": {"type":"object","properties":{"q_organization_name":{"type":"string"},"person_titles":{"type":"array","items":{"type":"string"}},"page":{"type":"integer"},"per_page":{"type":"integer"}}}},
        {"name": "organization_search", "description": "Live Apollo org search", "input_schema": {"type":"object","properties":{"q_organization_name":{"type":"string"},"page":{"type":"integer"}}}},
        {"name": "enrich_person", "description": "Enrich person by email/linkedin (live)", "input_schema": {"type":"object","properties":{"email":{"type":"string"},"linkedin_url":{"type":"string"}}}},
    ]

def call_tool(name: str, arguments: dict):
    if name == "people_search": return people_search(**arguments)
    if name == "organization_search": return organization_search(**arguments)
    if name == "enrich_person": return enrich_person(**arguments)
    raise ApolloError(f"Unknown tool {name}")
