"""Check Apollo MCP + REST — real probe, no mock.
Run: agents\Scripts\python.exe scripts/check_apollo_mcp.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from app.core.config import settings

print("APOLLO — 2 modes (both real)")
print(f"MCP_URL = https://mcp.apollo.io/mcp")
print(f"REST API_KEY = {'set' if settings.apollo_api_key else 'MISSING (set APOLLO_API_KEY in backend/.env for H1 REST)'}")

print(f"\n[1] MCP probe (Streamable HTTP, should be 401 without OAuth = REACHABLE)")
try:
    r = httpx.post("https://mcp.apollo.io/mcp",
        json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"yexis-check","version":"1.0"}}},
        headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"}, timeout=10)
    print(f"  POST initialize -> {r.status_code}")
    print(f"  Body: {r.text[:400]}")
    if r.status_code in (401, 407):
        print("  -> 401 REACHABLE but needs OAuth (normal). Do: claude mcp add --transport http apollo https://mcp.apollo.io/mcp")
    elif r.status_code == 200:
        print("  -> 200 REACHABLE")
    else:
        print(f"  -> Status {r.status_code}")
except Exception as e:
    print(f"  [FAIL] {e}")

print(f"\n[2] REST probe (if API key set)")
if not settings.apollo_api_key:
    print("  SKIP — no APOLLO_API_KEY in .env")
else:
    try:
        r = httpx.post("https://api.apollo.io/v1/mixed_people/search",
            json={"page":1,"per_page":1}, headers={"Content-Type":"application/json","Cache-Control":"no-cache","X-Api-Key": settings.apollo_api_key}, timeout=10)
        print(f"  POST /v1/mixed_people/search -> {r.status_code}")
        print(f"  Body: {r.text[:400]}")
        if r.status_code == 200:
            print("  -> REST REACHABLE (paid plan)")
        elif r.status_code == 403 and "Free plan" in r.text:
            print("  -> REST 403 Free plan = EXPECTED (Free cannot use API). Use MCP (above) for chat, or upgrade Apollo to Basic at https://www.apollo.io/pricing for headless H1.")
        elif r.status_code == 401:
            print("  -> 401 Invalid API key — check APOLLO_API_KEY in .env")
        else:
            print(f"  -> Status {r.status_code} — check key/plan")
        # Also test auth health endpoint which works on Free
        r2 = httpx.get("https://api.apollo.io/v1/auth/health", headers={"Cache-Control":"no-cache","X-Api-Key": settings.apollo_api_key}, timeout=10)
        print(f"  GET /v1/auth/health -> {r2.status_code} {r2.text[:200]}")
        if r2.status_code == 200:
            print("  -> API key VALID (health ok, just Free plan limited)")
    except Exception as e:
        print(f"  [FAIL] {e}")

print(f"\n[3] .mcp.json check")
import json as _j
p = Path(r"E:\ai_agent_platform\.mcp.json")
if p.exists():
    data = _j.loads(open(p,encoding="utf-8").read())
    print(f"  apollo in .mcp.json: {'apollo' in data.get('mcpServers',{})}")
    if "apollo" in data.get("mcpServers",{}):
        print(f"  -> {data['mcpServers']['apollo']}")
else:
    print("  Missing .mcp.json")

print("\nDone. MCP 401 = correct, REST needs API key.")
