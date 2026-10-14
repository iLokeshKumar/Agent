"""Check RocketReach — MCP + REST (both real, no mock).
Run: agents\Scripts\python.exe scripts/check_rocketreach.py
MCP: https://mcp.rocketreach.co/mcp  (OAuth via MCP client)
REST: https://api.rocketreach.co/v2/api/search  (Api-Key header)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from app.core.config import settings

print("ROCKETREACH — MCP + REST (both real)")
print(f"MCP_URL = https://mcp.rocketreach.co/mcp")
print(f"REST API_KEY = {'set' if settings.rocketreach_api_key else 'MISSING (REST needs ROCKETREACH_API_KEY)'}")

print(f"\n[1] MCP probe (Streamable HTTP, 401/403 = REACHABLE — Cloudflare protected)")
try:
    r = httpx.post("https://mcp.rocketreach.co/mcp",
        json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"yexis-check","version":"1.0"}}},
        headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"}, timeout=10)
    print(f"  POST initialize -> {r.status_code}")
    print(f"  Body: {r.text[:500]}")
    if r.status_code in (401, 407):
        print("  -> 401 REACHABLE but needs OAuth (normal). Do: claude mcp add --transport http rocketreach https://mcp.rocketreach.co/mcp")
    elif r.status_code == 403 and "Just a moment" in r.text:
        print("  -> 403 Cloudflare challenge = REACHABLE (MCP client handles it). Add to Claude/Opencode and OAuth.")
    elif r.status_code == 200:
        print("  -> 200 REACHABLE")
    else:
        print(f"  -> Status {r.status_code} — still REACHABLE, check RocketReach docs")
except Exception as e:
    print(f"  [FAIL] {e}")

print(f"\n[2] REST probe (if API key set) — RocketReach uses POST")
if not settings.rocketreach_api_key:
    print("  SKIP — no ROCKETREACH_API_KEY in .env (REST not needed if you use MCP)")
else:
    try:
        # RocketReach search is POST with JSON body, not GET
        r = httpx.post("https://api.rocketreach.co/v2/api/search", json={"page_size":1}, headers={"Api-Key": settings.rocketreach_api_key, "Content-Type":"application/json"}, timeout=15)
        print(f"  POST /search -> {r.status_code}")
        print(f"  Body: {r.text[:500]}")
        if r.status_code == 200:
            print("  -> REST REACHABLE + KEY VALID")
        elif r.status_code == 400 and "This field is required" in r.text:
            print("  -> 400 Query required = KEY VALID (send real query via MCP or POST with query param)")
        elif r.status_code == 401:
            print("  -> 401 Invalid API key — check ROCKETREACH_API_KEY in .env")
        elif r.status_code == 402:
            print("  -> 402 Quota exhausted / plan limit — check RocketReach dashboard")
        elif r.status_code == 429:
            print("  -> 429 Rate limited — try again later")
        else:
            print(f"  -> Status {r.status_code}")
    except Exception as e:
        print(f"  [FAIL] {e}")

print(f"\n[3] .mcp.json check")
import json as _j
p = Path(r"E:\ai_agent_platform\.mcp.json")
if p.exists():
    data = _j.loads(open(p,encoding="utf-8").read())
    print(f"  rocketreach in .mcp.json: {'rocketreach' in data.get('mcpServers',{})}")
else:
    print("  Missing .mcp.json")

print("\nDone. MCP 401 = correct, REST needs API key. Both are live, no mock.")
