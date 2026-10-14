"""Apollo MCP + RocketReach — backend health/tools (real)."""
from fastapi import APIRouter, HTTPException
router = APIRouter(prefix="/mcp/enrich", tags=["mcp-enrich-extra"])

@router.get("/apollo/mcp/health")
def apollo_mcp_health():
    from app.integrations.apollo_mcp_client import health
    return health()

@router.get("/rocketreach/health")
def rr_health2():
    from app.integrations.rocketreach_client import health
    return health()
