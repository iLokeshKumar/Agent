"""RocketReach + Apollo MCP — real, no mock."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/mcp/enrich", tags=["mcp-enrich"])

class RocketSearchRequest(BaseModel):
    query: str | None = None
    current_employer: str | None = None
    title: str | None = None
    location: str | None = None
    page: int = 1
    page_size: int = 10

@router.get("/rocketreach/health")
def rr_health():
    from app.core.config import settings
    return {"configured": bool(settings.rocketreach_api_key), "provider": "rocketreach"}

@router.post("/rocketreach/search")
def rr_search(req: RocketSearchRequest):
    try:
        from mcp_servers.rocketreach.server import person_search
        return person_search(query=req.query, current_employer=req.current_employer, title=req.title, location=req.location, page=req.page, page_size=req.page_size)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

class ApolloSearchRequest(BaseModel):
    q_organization_name: str | None = None
    person_titles: list[str] | None = None
    page: int = 1
    per_page: int = 25

@router.get("/apollo/health")
def apollo_health():
    from app.core.config import settings
    return {"configured": bool(settings.apollo_api_key), "provider": "apollo", "mcp_url": "https://mcp.apollo.io/mcp"}

@router.post("/apollo/search")
def apollo_search(req: ApolloSearchRequest):
    try:
        from mcp_servers.apollo.server import people_search
        return people_search(q_organization_name=req.q_organization_name, person_titles=req.person_titles, page=req.page, per_page=req.per_page)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
