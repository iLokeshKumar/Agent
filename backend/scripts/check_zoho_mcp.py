"""
Check if Zoho MCP servers (YEXIS and YE) are reachable and authenticated.

Run from backend/:
    agents\Scripts\python.exe scripts\check_zoho_mcp.py

Two auth paths are tested per server:
  A. Backend OAuth token (from config/zoho_tokens.json) — used by backend agents
  B. mcp-remote / Claude Desktop path — shows the OAuth URL if auth is needed

For Claude Desktop: add to .mcp.json:
    {"mcpServers": {"ZohoMCP": {"command": "npx", "args": [
        "mcp-remote", "<URL>", "--transport", "http-only"]}}}
"""

import json
import re
import sys
import time
from pathlib import Path

BACKEND    = Path(__file__).resolve().parent.parent
TOKEN_FILE = BACKEND / "config" / "zoho_tokens.json"
ENV_FILE   = BACKEND / ".env"


def load_env() -> dict:
    env = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.split(" #")[0].strip().strip('"').strip("'").strip()
        if val:
            env[key.strip()] = val
    return env


def get_token(env: dict) -> str | None:
    """Get valid access token from file, refresh if expired. Returns None if unavailable."""
    import httpx

    stored = json.loads(TOKEN_FILE.read_text()) if TOKEN_FILE.exists() else {}
    access = stored.get("access_token", "")
    expiry = stored.get("token_expiry", 0)

    if access and time.time() < expiry - 60:
        return access

    refresh = stored.get("refresh_token") or env.get("ZOHO_REFRESH_TOKEN", "")
    if not refresh:
        return None

    dc = env.get("ZOHO_DATA_CENTER", "in")
    token_url = {
        "in": "https://accounts.zoho.in/oauth/v2/token",
        "us": "https://accounts.zoho.com/oauth/v2/token",
        "eu": "https://accounts.zoho.eu/oauth/v2/token",
    }.get(dc, "https://accounts.zoho.in/oauth/v2/token")

    try:
        r = httpx.post(token_url, data={
            "refresh_token": refresh,
            "client_id":     env.get("ZOHO_CLIENT_ID", ""),
            "client_secret": env.get("ZOHO_CLIENT_SECRET", ""),
            "grant_type":    "refresh_token",
        }, timeout=20)
        body = r.json()
        if "access_token" in body:
            stored.update({
                "access_token": body["access_token"],
                "token_expiry": time.time() + body.get("expires_in", 3600),
            })
            TOKEN_FILE.write_text(json.dumps(stored, indent=2))
            return body["access_token"]
    except Exception:
        pass
    return None


def parse_mcp(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"data:\s*(\{.*\})", text, re.DOTALL)
        return json.loads(m.group(1)) if m else {}


def mcp_call(url: str, method: str, params: dict, token: str | None, session_id: str | None = None) -> tuple[int, dict, dict]:
    """Send one MCP JSON-RPC call. Returns (status_code, response_dict, response_headers)."""
    import httpx

    headers: dict = {
        "Content-Type": "application/json",
        "Accept":       "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Zoho-oauthtoken {token}"
    if session_id:
        headers["mcp-session-id"] = session_id

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=20)
        resp_headers = dict(r.headers)
        return r.status_code, parse_mcp(r.text), resp_headers
    except Exception as exc:
        return 0, {"error": str(exc)}, {}


def check_server(name: str, url: str, token: str | None) -> bool:
    SEP = "─" * 64
    print(f"\n{SEP}")
    print(f"  {name}")
    print(f"  {url}")
    print(SEP)

    # ── A. Test with backend OAuth token ──────────────────────────────────
    print("\n  [A] Backend OAuth token (zoho_books.py path)")
    if not token:
        print("      No token available — run OAuth at http://127.0.0.1:8000/auth/zoho")
        backend_ok = False
    else:
        print(f"      Token: {token[:32]}...")
        status, data, hdrs = mcp_call(url, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "yexis-backend", "version": "1.0"},
        }, token)

        if status == 200:
            session_id = hdrs.get("mcp-session-id") or hdrs.get("Mcp-Session-Id")
            server_info = data.get("result", {}).get("serverInfo", {})
            print(f"      initialize → 200 OK  serverInfo={server_info}")

            # tools/list
            status2, data2, _ = mcp_call(url, "tools/list", {}, token, session_id)
            if status2 == 200:
                tools = data2.get("result", {}).get("tools", [])
                print(f"      tools/list → 200 OK  ({len(tools)} tools)")
                for t in tools[:25]:
                    print(f"        • {t.get('name','?'):<42} {(t.get('description') or '')[:60]}")
                if len(tools) > 25:
                    print(f"        … and {len(tools)-25} more")
                backend_ok = True
            else:
                print(f"      tools/list → {status2}  {data2}")
                backend_ok = False
        elif status == 401:
            print("      initialize → 401 Unauthorized")
            print("      The backend OAuth token is NOT accepted by this MCP server.")
            print("      This MCP server requires separate authentication (see path B below).")
            backend_ok = False
        elif status == 0:
            print(f"      Connection error: {data.get('error')}")
            backend_ok = False
        else:
            print(f"      initialize → {status}  {data}")
            backend_ok = False

    # ── B. Test the mcp-remote / Claude Desktop path ──────────────────────
    print("\n  [B] mcp-remote path (Claude Desktop / npx)")
    status_noauth, data_noauth, hdrs_noauth = mcp_call(url, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "mcp-remote-check", "version": "1.0"},
    }, token=None)  # no auth — let server challenge us

    www_auth = hdrs_noauth.get("www-authenticate", "")
    if status_noauth == 401 and www_auth:
        print(f"      Server returned 401 + WWW-Authenticate (OAuth challenge) ✓")
        # Extract auth URL from header
        auth_url_match = re.search(r'resource="([^"]+)"', www_auth) or \
                         re.search(r"resource=([^\s,]+)", www_auth)
        oauth_meta_match = re.search(r'as_uri="([^"]+)"', www_auth) or \
                           re.search(r"as_uri=([^\s,]+)", www_auth)
        if oauth_meta_match:
            print(f"      OAuth server: {oauth_meta_match.group(1)}")
        print(f"      WWW-Authenticate: {www_auth[:200]}")
        print()
        print("      To authenticate via mcp-remote (one-time browser login):")
        print(f"        npx mcp-remote \"{url}\" --transport http-only")
        print()
        print("      For Claude Desktop, add to .mcp.json:")
        print('        {"mcpServers": {"ZohoMCP": {"command": "npx",')
        print(f'          "args": ["mcp-remote", "{url}", "--transport", "http-only"]}}}}')
        mcp_remote_ok = True  # URL is correct and responding with proper OAuth challenge
    elif status_noauth == 200:
        print("      Server accepts unauthenticated requests — unusual but OK")
        mcp_remote_ok = True
    elif status_noauth == 0:
        print(f"      Connection error: {data_noauth.get('error')}")
        mcp_remote_ok = False
    else:
        print(f"      → HTTP {status_noauth}: {data_noauth}")
        mcp_remote_ok = status_noauth in (401, 403)

    return backend_ok or mcp_remote_ok


def main():
    print("=" * 64)
    print("  Zoho MCP Server Check")
    print("=" * 64)

    env   = load_env()

    servers = {
        "YEXIS": env.get("ZOHO_MCP_SERVER_URL_YEXIS", ""),
        "YE":    env.get("ZOHO_MCP_SERVER_URL_YE", ""),
    }
    active = {k: v for k, v in servers.items() if v}

    if not active:
        sys.exit("No MCP URLs in .env — set ZOHO_MCP_SERVER_URL_YEXIS / ZOHO_MCP_SERVER_URL_YE")

    print(f"\nServers : {', '.join(active)}")
    print("Getting backend token (for path A)...")
    token = get_token(env)
    if token:
        print(f"  Token  : {token[:32]}...")
    else:
        print("  No token available — only path B will be tested")

    results = {}
    for name, url in active.items():
        results[name] = check_server(name, url, token)

    print(f"\n{'=' * 64}")
    print("  SUMMARY")
    print("=" * 64)
    for name, ok in results.items():
        mark = "REACHABLE ✓" if ok else "UNREACHABLE ✗"
        print(f"  {name:<10}  {mark}")
    print()


if __name__ == "__main__":
    main()
