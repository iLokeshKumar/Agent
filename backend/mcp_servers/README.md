# MCP — Model Context Protocol Tools

MCP servers expose tools that agents call via `app/api/mcp_*` routers.
All servers below are REAL (live APIs, no stubs, no mock data).

## Status

| Server | Path | Status | Router | Description |
|--------|------|--------|--------|-------------|
| **Tally** | `mcp/tally/` | ✅ Real | `app/api/mcp_tally.py` → `app/integrations/tally_gateway.py:HttpTallyTransport` | Stage → Approve → POST to `http://TALLY_HOST:9000` (fails fast if unreachable) |
| **Local DB** | `mcp/local_db/` | ✅ Real | `app/api/mcp_local_db.py` | Typed tools over real Postgres `yexis` (no raw SQL from agents) |
| **WhatsApp** | `mcp/whatsapp/` | ✅ Real | `app/api/mcp_whatsapp.py` → `mcp/whatsapp/server.py` | `send_template`/`send_session_message` via live Gallabox/Gupshup/Twilio/Meta (fails if `WABA_*` missing) |
| **RocketReach** | `mcp/rocketreach/` | ✅ Real | `app/api/mcp_rocketreach.py` → `mcp/rocketreach/server.py` | `person_search`/`lookup`/`company_search` via `https://api.rocketreach.co/v2/api/*` (`ROCKETREACH_API_KEY`) |
| **Apollo** | `mcp/apollo/` | ✅ Real | `app/api/mcp_rocketreach.py` → `mcp/apollo/server.py` | `people_search`/`enrich` via `https://api.apollo.io/v1` (`APOLLO_API_KEY`) + MCP `https://mcp.apollo.io/mcp` for Cowork |
| **Cloud DB** | `mcp/cloud_db/` | 🚧 Planned | — | Read-only cloud replica (not needed until Local→Cloud shipper done) |

Stubs are empty directories with no router — they are ignored at runtime and do not affect tests.

## Adding a new MCP server

1. Create `mcp/<name>/server.py` with `list_tools()` / `call_tool()`.
2. Add router `app/api/mcp_<name>.py` and include in `app/main.py`.
3. Update this table.
