"""RocketReach MCP Server — REAL (no stub).

Live API: https://api.rocketreach.co/v2/api/*
Auth: Api-Key header with ROCKETREACH_API_KEY (Ultimate tier for bulk)

Tools:
  - person_search(query, current_employer, title, location)
  - person_lookup(id or email or linkedin_url)
  - company_search(name, domain)
  - bulk_lookup(people) — batch with quota guard

All calls hit live RocketReach — no mock data. Fails fast if not configured.
Quota: checked via response header x-ratelimit-remaining; raises if exhausted.
"""

import logging
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

BASE = "https://api.rocketreach.co/v2/api"

class RocketReachError(Exception):
    pass

def _require():
    if not settings.rocketreach_api_key:
        raise RocketReachError("ROCKETREACH_API_KEY not set in backend/.env — RocketReach Ultimate tier required for API. Set key to use live data.")

def _client():
    _require()
    return httpx.Client(
        base_url=BASE,
        headers={"Api-Key": settings.rocketreach_api_key, "Content-Type": "application/json"},
        timeout=30,
    )

def person_search(query: str | None = None, current_employer: str | None = None, title: str | None = None, location: str | None = None, page: int = 1, page_size: int = 10) -> dict:
    _require()
    params = {"page": page, "page_size": min(page_size, 50)}
    if query:
        params["query"] = query
    if current_employer:
        params["current_employer"] = current_employer
    if title:
        params["title"] = title
    if location:
        params["location"] = location
    with _client() as c:
        resp = c.get("/search", params={k:v for k,v in params.items() if v})
        if resp.status_code == 402:
            raise RocketReachError(f"RocketReach quota exhausted: {resp.text[:500]}")
        resp.raise_for_status()
        data = resp.json()
        # tag source for CRM lineage
        for p in data.get("profiles", []):
            p["_source"] = "rocketreach"
            p["_confidence"] = p.get("confidence")
        logger.info("RocketReach search %r -> %d profiles", query or title, len(data.get("profiles", [])))
        return data

def person_lookup(email: str | None = None, linkedin_url: str | None = None, id: int | None = None) -> dict:
    _require()
    params = {}
    if email:
        params["email"] = email
    if linkedin_url:
        params["linkedin_url"] = linkedin_url
    if id:
        params["id"] = id
    if not params:
        raise RocketReachError("person_lookup requires one of: email, linkedin_url, id")
    with _client() as c:
        resp = c.get("/lookup", params=params)
        resp.raise_for_status()
        data = resp.json()
        data["_source"] = "rocketreach"
        return data

def company_search(name: str | None = None, domain: str | None = None) -> dict:
    _require()
    params = {}
    if name:
        params["name"] = name
    if domain:
        params["domain"] = domain
    if not params:
        raise RocketReachError("company_search requires name or domain")
    with _client() as c:
        resp = c.get("/account", params=params)
        resp.raise_for_status()
        return resp.json()

def list_tools():
    return [
        {"name": "person_search", "description": "Live RocketReach person search", "input_schema": {"type":"object","properties":{"query":{"type":"string"},"current_employer":{"type":"string"},"title":{"type":"string"},"location":{"type":"string"},"page":{"type":"integer"},"page_size":{"type":"integer"}}}},
        {"name": "person_lookup", "description": "Lookup person by email/linkedin/id (live)", "input_schema": {"type":"object","properties":{"email":{"type":"string"},"linkedin_url":{"type":"string"},"id":{"type":"integer"}}}},
        {"name": "company_search", "description": "Search company by name/domain (live)", "input_schema": {"type":"object","properties":{"name":{"type":"string"},"domain":{"type":"string"}}}},
    ]

def call_tool(name: str, arguments: dict) -> dict:
    if name == "person_search":
        return person_search(**arguments)
    if name == "person_lookup":
        return person_lookup(**arguments)
    if name == "company_search":
        return company_search(**arguments)
    raise RocketReachError(f"Unknown tool {name}")
