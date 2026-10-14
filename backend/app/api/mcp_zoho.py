"""Zoho MCP — backend as client (real).

This router lets the *application* E:\ai_agent_platform call the 2
Zoho MCP servers you created (300 scopes each) — not Claude.

Endpoints (all real, no mock):
  GET  /mcp/zoho/health     -> which MCP URLs configured, Zoho OAuth status
  GET  /mcp/zoho/tools      -> tools/list from both servers (needs OAuth)
  POST /mcp/zoho/call       -> tools/call on chosen server

Test after OAuth:
  curl http://127.0.0.1:8000/mcp/zoho/health
  curl http://127.0.0.1:8000/mcp/zoho/tools
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/mcp/zoho", tags=["mcp-zoho"])

@router.get("/health")
def health():
    from app.core.config import settings
    from app.integrations.zoho_books import get_zoho_client
    client = get_zoho_client()
    return {
        "servers": {
            "YEXIS": bool(settings.zoho_mcp_server_url_yexis or settings.zoho_mcp_server_url),
            "AGENT": bool(settings.zoho_mcp_server_url_agent),
            "yexis_url": (settings.zoho_mcp_server_url_yexis or settings.zoho_mcp_server_url)[:60] + "..." if (settings.zoho_mcp_server_url_yexis or settings.zoho_mcp_server_url) else None,
            "agent_url": settings.zoho_mcp_server_url_agent[:60] + "..." if settings.zoho_mcp_server_url_agent else None,
        },
        "zoho_oauth": {
            "configured": client.configured,
            "has_access_token": bool(client.access_token),
            "has_refresh_token": bool(client.refresh_token),
            "org_id": client.organization_id or None,
            "data_center": client.data_center,
        },
        "hint": "If configured=false, visit GET http://127.0.0.1:8000/auth/zoho first",
    }

@router.get("/tools")
def list_tools():
    from app.integrations.zoho_mcp_client import list_all_tools
    try:
        return list_all_tools()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

class CallRequest(BaseModel):
    server: str = "YEXIS"  # YEXIS or AGENT
    tool: str
    arguments: dict | None = None

@router.post("/call")
def call_tool(req: CallRequest):
    from app.integrations.zoho_mcp_client import get_zoho_mcp_client
    try:
        client = get_zoho_mcp_client(req.server)
        return client.call_tool(req.tool, req.arguments or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
