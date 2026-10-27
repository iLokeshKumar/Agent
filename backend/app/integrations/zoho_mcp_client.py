"""Zoho MCP client for backend — REAL (no mock, no stub).

Your 2 Zoho MCP servers (300 scopes each) expose Books+CRM tools over
Streamable HTTP (zohomcp.com). This client lets the *backend*
(E:\ai_agent_platform\backend) call them directly — not just Claude.

Flow (real):
  backend --httpx--> Zoho MCP (initialize -> tools/list -> tools/call)
  Auth: the key embedded in each server URL (/mcp/<key>/message) is enough.
        If the optional MCP connector OAuth (app/api/auth_zoho_mcp.py) has
        been completed, its token is sent as Authorization: Zoho-oauthtoken.
        The Books/CRM REST token is never sent — the MCP servers reject it (401).

Usage:
  from app.integrations.zoho_mcp_client import get_zoho_mcp_client
  c = get_zoho_mcp_client("YEXIS")  # or "AGENT" or None for both
  tools = c.list_tools()            # real MCP tools/list
  result = c.call_tool("get_invoices", {"organization_id": "..."})

If ZOHO_MCP_* not set or Zoho not authenticated, raises with clear
instruction — never returns fake tools.
"""

import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

# Map name -> env URL
def _urls():
    return {
        "YEXIS": settings.zoho_mcp_server_url_yexis or settings.zoho_mcp_server_url,
        "AGENT": settings.zoho_mcp_server_url_agent,
    }

def _pick_url(name: str | None = None) -> tuple[str, str]:
    urls = _urls()
    if name:
        url = urls.get(name.upper())
        if not url:
            raise ValueError(f"Unknown MCP server {name!r} — pick YEXIS or AGENT, have {list(urls.keys())}")
        return name.upper(), url
    # default: first configured
    for k, v in urls.items():
        if v:
            return k, v
    raise ValueError("No Zoho MCP URL configured — set ZOHO_MCP_SERVER_URL_YEXIS and/or _AGENT in backend/.env")

class ZohoMCPClient:
    def __init__(self, server_name: str, server_url: str):
        self.server_name = server_name
        self.server_url = server_url
        self._session_id: str | None = None

    def _headers(self) -> dict:
        # The URL key authenticates on its own. The Books/CRM REST token must
        # NOT be sent: the MCP servers reject it with 401. Only attach the
        # connector token if the optional MCP OAuth flow has been completed.
        from app.integrations.zoho_mcp_auth import mcp_access_token, ZohoMcpAuthError
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            headers["Authorization"] = f"Zoho-oauthtoken {mcp_access_token()}"
        except ZohoMcpAuthError:
            pass
        return headers

    def initialize(self) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "yexis-backend", "version": "1.0"},
            },
        }
        r = httpx.post(self.server_url, json=payload, headers=self._headers(), timeout=30)
        # Zoho may respond with SSE stream; httpx will capture text
        if r.status_code == 401:
            raise RuntimeError(f"Zoho MCP {self.server_name} 401 — the server URL key was rejected, or the MCP OAuth token at /auth/zoho-mcp/status has expired. Re-copy the server URL from the Zoho MCP console.")
        r.raise_for_status()
        # Try parse JSON, fallback to text
        try:
            data = r.json()
            # store session id if returned in header
            self._session_id = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
            return data
        except Exception:
            # SSE format: "event: message\ndata: {...}"
            text = r.text
            import re, json as _j
            m = re.search(r"data:\s*(\{.*\})", text, re.DOTALL)
            if m:
                return _j.loads(m.group(1))
            return {"raw": text[:2000]}

    def list_tools(self) -> list[dict]:
        # Ensure initialized
        self.initialize()
        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        headers = self._headers()
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        r = httpx.post(self.server_url, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        try:
            data = r.json()
            # MCP returns {"result": {"tools": [...]}}
            if isinstance(data, dict) and "result" in data:
                return data["result"].get("tools", [])
            # SSE fallback
            import re, json as _j
            m = re.search(r"data:\s*(\{.*\})", r.text, re.DOTALL)
            if m:
                j = _j.loads(m.group(1))
                return j.get("result", {}).get("tools", [])
            return []
        except Exception as exc:
            raise RuntimeError(f"Failed to parse tools/list from {self.server_name}: {exc} — raw: {r.text[:800]}") from exc

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict:
        self.initialize()
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        }
        headers = self._headers()
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        r = httpx.post(self.server_url, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"raw": r.text[:3000]}

# Singleton helpers

_clients: dict[str, ZohoMCPClient] = {}

def get_zoho_mcp_client(name: str | None = None) -> ZohoMCPClient:
    """Get client for YEXIS or AGENT or first available. Real, no mock."""
    key, url = _pick_url(name)
    if key not in _clients:
        _clients[key] = ZohoMCPClient(key, url)
    return _clients[key]

def list_all_tools() -> dict[str, list[dict]]:
    """List tools from both servers (real)."""
    result = {}
    for name in ["YEXIS", "AGENT"]:
        url = _urls()[name]
        if not url:
            continue
        try:
            result[name] = get_zoho_mcp_client(name).list_tools()
        except Exception as exc:
            result[name] = [{"error": str(exc)}]  # surface real error, not fake empty
    return result
