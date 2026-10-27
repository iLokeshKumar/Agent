"""Zoho MCP connector OAuth — authorization-code (PKCE) leg.

Flow (one-time, browser):
  1. GET /auth/zoho-mcp/start     → redirects to the connector's authorize URL
  2. GET /auth/zoho-mcp/callback  → exchanges the code for access+refresh tokens
  3. GET /auth/zoho-mcp/status    → shows current MCP auth state

This is the connector flow modelled on the consent URL Zoho issues for MCP
clients, e.g.
  https://mcp.zoho.in/mcp-client/<hash>/<connector>/oauth
     ?response_type=code&client_id=...&redirect_uri=...&scope=...
     &code_challenge=...&code_challenge_method=S256&state=...&resource=<mcp msg url>

The authorize URL, client_id/secret, scopes and resource come from registering
a client for the MCP server in the Zoho MCP admin console — see settings
zoho_mcp_* and backend/.env.example. Until those are set, /start returns a 400
explaining what is missing rather than redirecting nowhere.
"""

import base64
import hashlib
import logging
import os
import secrets
import time
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import settings
from app.integrations.zoho_mcp_auth import save_tokens, status as mcp_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/zoho-mcp", tags=["auth-zoho-mcp"])

# Transient PKCE state: state -> (code_verifier, created_ts). In-memory is fine —
# this is a single-operator admin flow completed within one browser round-trip.
_PENDING: dict[str, tuple[str, float]] = {}
_PENDING_TTL = 600  # 10 min


def _prune() -> None:
    now = time.time()
    for s in [s for s, (_, ts) in _PENDING.items() if now - ts > _PENDING_TTL]:
        _PENDING.pop(s, None)


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


@router.get("/start")
def start():
    """Redirect the operator to the Zoho MCP connector consent page."""
    missing = [
        name for name, val in (
            ("zoho_mcp_client_id", settings.zoho_mcp_client_id),
            ("zoho_mcp_authorize_url", settings.zoho_mcp_authorize_url),
            ("zoho_mcp_scopes", settings.zoho_mcp_scopes),
            ("zoho_mcp_resource", settings.zoho_mcp_resource),
        ) if not val
    ]
    if missing:
        raise HTTPException(
            400,
            "Zoho MCP OAuth is not configured. Register a client for the MCP "
            "server in the Zoho MCP admin console, then set these in backend/.env: "
            + ", ".join(missing),
        )

    _prune()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    _PENDING[state] = (verifier, time.time())

    params = {
        "response_type": "code",
        "client_id": settings.zoho_mcp_client_id,
        "redirect_uri": settings.zoho_mcp_redirect_uri,
        "scope": settings.zoho_mcp_scopes,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": settings.zoho_mcp_resource,
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"{settings.zoho_mcp_authorize_url}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url)


def _page(title: str, body: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<html><body style='font-family:system-ui;max-width:640px;margin:40px auto'>"
        f"<h2>{title}</h2>{body}</body></html>",
        status_code=code,
    )


@router.get("/callback")
def callback(code: str | None = None, state: str | None = None,
             error: str | None = None, error_description: str | None = None):
    """Exchange the authorization code for MCP access + refresh tokens."""
    if error:
        return _page("❌ Authorization failed",
                     f"<p>{error}: {error_description or ''}</p>", 400)
    if not code or not state:
        return _page("❌ Missing code/state",
                     "<p>Zoho did not return an authorization code.</p>", 400)

    _prune()
    pending = _PENDING.pop(state, None)
    if not pending:
        return _page("❌ Expired or unknown request",
                     "<p>Start again at <code>/auth/zoho-mcp/start</code>.</p>", 400)
    verifier, _ = pending

    try:
        resp = httpx.post(
            settings.zoho_mcp_token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.zoho_mcp_client_id,
                "client_secret": settings.zoho_mcp_client_secret,
                "redirect_uri": settings.zoho_mcp_redirect_uri,
                "code_verifier": verifier,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("MCP token exchange failed: %s", exc)
        return _page("❌ Token exchange failed", f"<p>{exc}</p>", 400)

    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        return _page("❌ Incomplete token response",
                     f"<p>Zoho did not return both tokens: {list(data.keys())}. "
                     "Ensure the consent included offline access.</p>", 400)

    save_tokens(access, refresh, data.get("expires_in", 3600), data.get("scope"))
    logger.info("Zoho MCP OAuth completed; tokens stored.")
    return _page("✅ Zoho MCP connected",
                 "<p>Access + refresh tokens stored. The backend can now call "
                 "the Zoho MCP server. You can close this window.</p>")


@router.get("/status")
def status():
    return mcp_status()
