"""Apollo MCP client for backend — REAL (Streamable HTTP, no mock).

Apollo MCP: https://mcp.apollo.io/mcp  (OAuth 2.0, not API key)
Your .mcp.json already has it:
  "apollo": { "url": "https://mcp.apollo.io/mcp" }

For headless lanes that need API key (H1 sourcing), use app/mcp/apollo/server.py
which calls REST https://api.apollo.io/v1 with APOLLO_API_KEY.
This client is for conversational/oagentic use via MCP (same 401->200 after OAuth).
"""

import httpx
from app.core.config import settings

MCP_URL = "https://mcp.apollo.io/mcp"

class ApolloMCPError(Exception):
    pass

def list_tools_via_mcp() -> list[dict]:
    """Real MCP tools/list — requires OAuth via MCP client (Claude/Opencode) first.
    From backend, without OAuth it will 401 — which is expected and means REACHABLE.
    """
    # Try unauthenticated probe first — Apollo MCP should return 401 with auth URL
    try:
        r = httpx.post(
            MCP_URL,
            json={"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}},
            headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"},
            timeout=10,
        )
        if r.status_code == 401:
            raise ApolloMCPError(f"Apollo MCP 401 — OAuth required. Connect via Claude Code: claude mcp add --transport http apollo {MCP_URL} then browser OAuth. Body: {r.text[:300]}")
        r.raise_for_status()
        data = r.json()
        return data.get("result", {}).get("tools", [])
    except httpx.HTTPStatusError as e:
        raise ApolloMCPError(f"Apollo MCP HTTP {e.response.status_code}: {e.response.text[:500]}") from e

def health() -> dict:
    return {
        "mcp_url": MCP_URL,
        "in_mcp_json": True,
        "api_key_set": bool(settings.apollo_api_key),
        "rest_health": "REST uses APOLLO_API_KEY in .env for headless H1; MCP uses OAuth via Claude/Opencode",
    }
