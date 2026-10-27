"""Zoho MCP connector OAuth — token store + refresh.

The Zoho MCP servers (ye-*.zohomcp.in etc.) reject the Books/CRM REST access
token with 401. They require a token issued to a *connector* registered in the
Zoho MCP admin console, carrying the ZohoMCP.tool.execute scope and bound to the
server's message URL (the `resource`).

This module owns that MCP-specific token: it persists access+refresh tokens to
config/zoho_mcp_tokens.json and refreshes against settings.zoho_mcp_token_url
(the standard accounts.zoho.in token endpoint). The authorization-code leg lives
in app/api/auth_zoho_mcp.py; this module is what the MCP client calls to get a
live bearer token.

Nothing here invents a token — if MCP OAuth was never completed, mcp_access_token()
raises with a clear instruction to visit /auth/zoho-mcp/start.
"""

import json
import logging
import threading
import time
from pathlib import Path

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TOKEN_FILE = Path(__file__).resolve().parents[2] / "config" / "zoho_mcp_tokens.json"
_REFRESH_BUFFER = 300  # refresh when <5 min of life remains
_lock = threading.Lock()


class ZohoMcpAuthError(RuntimeError):
    pass


def _read() -> dict:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            logger.warning("Could not read MCP token file: %s", exc)
    return {}


def _write(data: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_tokens(access_token: str, refresh_token: str | None, expires_in: int,
                scope: str | None = None) -> None:
    """Persist tokens from an authorization-code or refresh exchange. A refresh
    response may omit refresh_token — keep the existing one in that case."""
    with _lock:
        data = _read()
        data["access_token"] = access_token
        if refresh_token:
            data["refresh_token"] = refresh_token
        data["token_expiry"] = time.time() + int(expires_in or 3600)
        if scope:
            data["scope"] = scope
        _write(data)


def has_tokens() -> bool:
    return bool(_read().get("refresh_token") or _read().get("access_token"))


def status() -> dict:
    data = _read()
    expiry = data.get("token_expiry", 0)
    return {
        "configured": bool(settings.zoho_mcp_client_id and settings.zoho_mcp_authorize_url),
        "has_access_token": bool(data.get("access_token")),
        "has_refresh_token": bool(data.get("refresh_token")),
        "expires_in_seconds": max(0, int(expiry - time.time())) if expiry else 0,
        "scope": data.get("scope"),
    }


def _refresh(refresh_token: str) -> str:
    if not (settings.zoho_mcp_client_id and settings.zoho_mcp_client_secret):
        raise ZohoMcpAuthError(
            "MCP OAuth not configured — set zoho_mcp_client_id / zoho_mcp_client_secret."
        )
    resp = httpx.post(
        settings.zoho_mcp_token_url,
        data={
            "grant_type": "refresh_token",
            "client_id": settings.zoho_mcp_client_id,
            "client_secret": settings.zoho_mcp_client_secret,
            "refresh_token": refresh_token,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ZohoMcpAuthError(f"MCP token refresh failed ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    if "access_token" not in data:
        raise ZohoMcpAuthError(f"No access_token in MCP refresh response: {data}")
    save_tokens(
        data["access_token"],
        data.get("refresh_token"),
        data.get("expires_in", 3600),
        data.get("scope"),
    )
    logger.debug("Zoho MCP token refreshed, expires in %ss", data.get("expires_in"))
    return data["access_token"]


def mcp_access_token() -> str:
    """Return a live MCP access token, refreshing if near expiry. Raises
    ZohoMcpAuthError if OAuth was never completed."""
    with _lock:
        data = _read()
    access = data.get("access_token", "")
    expiry = data.get("token_expiry", 0)
    if access and time.time() < expiry - _REFRESH_BUFFER:
        return access

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise ZohoMcpAuthError(
            "Zoho MCP is not authorized. Complete the one-time consent at "
            "GET /auth/zoho-mcp/start (requires zoho_mcp_client_id, "
            "zoho_mcp_authorize_url and related settings)."
        )
    return _refresh(refresh_token)
