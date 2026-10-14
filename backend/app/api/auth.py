"""Zoho Books OAuth endpoints.

Flow:
  1. GET  /auth/zoho          → redirects user to Zoho login
  2. GET  /auth/zoho/callback → exchanges code for tokens
  3. GET  /auth/zoho/status   → shows current auth state
  4. POST /auth/zoho/revoke   → clears tokens
"""

import logging
import secrets
import html
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import jwt

from app.core.config import settings
from app.core.auth import ALGORITHM, _secret, decode_token, get_current_user
from app.integrations.zoho_books import (
    get_zoho_client,
    ZohoBooksError,
    ZOHO_TOKEN_URLS,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auth/zoho",
    tags=["auth"],
)


@router.get("/user/connect")
def zoho_user_connect(request: Request):
    """Begin an OAuth connection owned by the signed-in user."""
    actor = get_current_user(request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in before connecting Zoho.")
    client = get_zoho_client()
    if not client.client_id or not client.client_secret:
        raise HTTPException(
            status_code=503,
            detail="Add ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET to server .env first.",
        )
    if not settings.zoho_token_encryption_key:
        raise HTTPException(
            status_code=503,
            detail="Per-user Zoho connections require ZOHO_TOKEN_ENCRYPTION_KEY in server .env.",
        )
    nonce = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    state = jwt.encode(
        {
            "sub": actor.username,
            "uid": actor.user_id,
            "purpose": "zoho_user_connect",
            "nonce": nonce,
            "exp": expires_at,
        },
        _secret(),
        algorithm=ALGORITHM,
    )
    from app.services.zoho_connections import create_user_oauth_state
    create_user_oauth_state(actor.user_id, nonce, expires_at)
    auth_url = ZOHO_AUTH_URLS.get(client.data_center, ZOHO_AUTH_URLS["us"])
    params = {
        "scope": " ".join(ZOHO_SCOPES),
        "client_id": client.client_id,
        "response_type": "code",
        "redirect_uri": settings.zoho_redirect_uri,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return RedirectResponse(f"{auth_url}?{urlencode(params)}")


@router.get("/user/status")
def zoho_user_status(request: Request):
    actor = get_current_user(request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in required.")
    from app.services.zoho_connections import user_zoho_status
    return user_zoho_status(actor.username)


@router.post("/user/revoke")
def zoho_user_revoke(request: Request):
    actor = get_current_user(request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in required.")
    from app.services.zoho_connections import disconnect_user_zoho
    return {
        "status": "disconnected" if disconnect_user_zoho(actor.username) else "not_connected"
    }

# OAuth scopes — covers all 31 agents across Purchase, Finance, Stores, HR, Campaigns, Dashboard.
# ZohoBooks.fullaccess.all is the only guaranteed-valid Books scope that covers every module;
# individual module names like "vendorpayments", "banking", "creditnotes" are NOT valid scope
# identifiers and cause "Scope does not exist" from Zoho's auth server.
ZOHO_SCOPES = [
    "ZohoBooks.fullaccess.all",   # all Books modules — POs, bills, invoices, contacts, items…
    "ZohoCRM.modules.ALL",        # all CRM module CRUD — leads, contacts, deals, activities
    "ZohoCRM.users.READ",         # read CRM user list (M1/M2 assignment)
]

# Zoho OAuth authorize endpoints per data center
ZOHO_AUTH_URLS = {
    "us": "https://accounts.zoho.com/oauth/v2/auth",
    "eu": "https://accounts.zoho.eu/oauth/v2/auth",
    "in": "https://accounts.zoho.in/oauth/v2/auth",
    "com.au": "https://accounts.zoho.com.au/oauth/v2/auth",
    "jp": "https://accounts.zoho.jp/oauth/v2/auth",
}


@router.get("")
def zoho_login():
    """Redirect user to Zoho's OAuth consent page.

    Visit this URL in a browser to authorize the app.
    """
    client = get_zoho_client()

    if not client.client_id:
        raise HTTPException(
            status_code=500,
            detail=(
                "ZOHO_CLIENT_ID not set. "
                "Add it to your .env file."
            ),
        )

    auth_url = ZOHO_AUTH_URLS.get(
        client.data_center,
        ZOHO_AUTH_URLS["us"],
    )

    scopes = " ".join(ZOHO_SCOPES)

    redirect_uri = settings.zoho_redirect_uri

    params = (
        f"?scope={scopes}"
        f"&client_id={client.client_id}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    full_url = f"{auth_url}{params}"

    return RedirectResponse(url=full_url)


def _zoho_callback_page(content: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(content=content, status_code=status_code)


@router.get("/callback")
def zoho_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    accounts_server: str | None = None,
    state: str | None = None,
):
    """Handle the OAuth callback from Zoho.

    Exchanges the authorization code for access + refresh tokens.
    """
    user_state = decode_token(state) if state else None
    if state and (
        not user_state or user_state.get("purpose") != "zoho_user_connect"
        or not user_state.get("uid") or not user_state.get("sub")
        or not user_state.get("nonce")
    ):
        return _zoho_callback_page(
            "<html><body><h2>Connection could not be verified</h2>"
            "<p>Start the Zoho connection again from your signed-in account.</p></body></html>",
            status_code=400,
        )
    if user_state:
        from app.services.zoho_connections import consume_user_oauth_state
        if not consume_user_oauth_state(
            user_state["uid"], user_state["sub"], user_state["nonce"]
        ):
            return _zoho_callback_page(
                "<html><body><h2>Connection request expired</h2>"
                "<p>Start the Zoho connection again from your signed-in account.</p></body></html>",
                status_code=400,
            )

    if error:
        return _zoho_callback_page(
            f"""
            <html><body>
            <h2>❌ Authorization Failed</h2>
            <p><b>Error:</b> {"Zoho authorization was declined." if user_state else html.escape(error)}</p>
            <p><b>Description:</b> {"Reconnect from your account and approve the requested access." if user_state else html.escape(error_description or 'None')}</p>
            <p>You can close this window and try again.</p>
            </body></html>
            """,
            status_code=400,
        )

    if not code:
        return _zoho_callback_page(
            """
            <html><body>
            <h2>❌ No Authorization Code</h2>
            <p>Zoho did not return an authorization code.</p>
            <p>You can close this window and try again.</p>
            </body></html>
            """,
            status_code=400,
        )

    client = get_zoho_client()

    try:
        # Exchange code for tokens
        import httpx

        token_url = ZOHO_TOKEN_URLS.get(
            client.data_center,
            ZOHO_TOKEN_URLS["us"],
        )

        redirect_uri = settings.zoho_redirect_uri

        response = httpx.post(
            token_url,
            data={
                "code": code,
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()

        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")
        expires_in = data.get("expires_in", 3600)

        if not access_token or not refresh_token:
            raise ZohoBooksError(
                0,
                "Missing tokens in response",
            )

        if user_state:
            from app.services.zoho_connections import save_user_zoho_connection
            user_client = type(client)(
                access_token=access_token,
                refresh_token=refresh_token,
                organization_id="__discover__",
                data_center=client.data_center,
                client_id=client.client_id,
                client_secret=client.client_secret,
                token_save_callback=lambda *_args: None,
            )
            user_client._token_expiry = time.time() + int(expires_in)
            org = user_client.get_organization()
            org_id = str(org.get("organization_id") or "")
            if not org_id:
                raise ZohoBooksError(0, "Zoho did not return an accessible organization")
            save_user_zoho_connection(
                user_id=user_state["uid"],
                refresh_token=user_client.refresh_token,
                organization_id=org_id,
                organization_name=org.get("name") or org.get("company_name"),
                data_center=client.data_center,
            )
            return _zoho_callback_page(
                (
                    "<html><body><h2>Zoho connected to your account</h2>"
                    f"<p>Organization: {html.escape(str(org.get('name') or org_id))}</p>"
                    "<p>This connection is used for tasks you submit. If you disconnect, "
                    "the platform falls back to the shared server .env connection.</p>"
                    "<script>if(window.opener){window.opener.location.reload();window.close()}</script>"
                    "<p>You may close this window.</p></body></html>"
                )
            )

        # Set and persist tokens
        client.set_tokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

        # Try to get org info — org_id is already known from settings;
        # the API call just enriches with the display name.
        org_id = client.organization_id
        org_name = ""
        try:
            import time as _time
            _time.sleep(1)  # brief pause — token needs ~1s to be accepted by /organizations
            org = client.get_organization()
            org_name = org.get("name") or org.get("company_name") or ""
            file_org_id = org.get("organization_id", "")
            if file_org_id:
                org_id = file_org_id
                client.organization_id = org_id
                client.save_tokens()
        except Exception:
            pass  # non-fatal — org_id still shown below

        org_info = (
            f"<p><b>Organization:</b> {org_name or org_id}</p>"
            f"<p><b>Org ID:</b> {org_id}</p>"
        )

        logger.info(
            "Zoho OAuth completed successfully. "
            "Org: %s",
            client.organization_id,
        )

        return _zoho_callback_page(
            f"""
            <html><body>
            <h2>✅ Zoho Books Connected!</h2>
            {org_info}
            <p><b>Access token:</b> expires in {expires_in}s</p>
            <p><b>Refresh token:</b> saved</p>
            <hr>
            <p>You can close this window. The platform can now
            fetch data from Zoho Books.</p>
            <p><a href="/docs">Go to API docs →</a></p>
            </body></html>
            """,
            status_code=200,
        )

    except httpx.HTTPStatusError as exc:
        error_body = exc.response.text
        logger.exception("Zoho token exchange failed: %s", error_body)

        return _zoho_callback_page(
            f"""
            <html><body>
            <h2>❌ Token Exchange Failed</h2>
            <p><b>Status:</b> {exc.response.status_code}</p>
            <p><b>Response:</b> {html.escape(error_body)}</p>
            <p>Check your ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET.</p>
            </body></html>
            """,
            status_code=400,
        )

    except Exception as exc:
        logger.exception("Zoho OAuth callback error")

        return _zoho_callback_page(
            f"""
            <html><body>
            <h2>❌ Unexpected Error</h2>
            <p>{html.escape(str(exc))}</p>
            </body></html>
            """,
            status_code=500,
        )


@router.get("/status")
def zoho_status():
    """Show current Zoho Books auth status."""
    client = get_zoho_client()

    # Check if tokens file exists
    from app.integrations.zoho_books import TOKEN_FILE
    token_file_exists = TOKEN_FILE.exists()

    # Test connection by fetching org info
    connection_ok = False
    org_info = None
    error_msg = None

    if client.configured:
        try:
            org_info = client.get_organization()
            connection_ok = True
        except ZohoBooksError as exc:
            error_msg = str(exc)

    return {
        "configured": client.configured,
        "connection_ok": connection_ok,
        "data_center": client.data_center,
        "organization_id": client.organization_id or None,
        "has_access_token": bool(client.access_token),
        "has_refresh_token": bool(client.refresh_token),
        "token_file_exists": token_file_exists,
        "org_info": org_info,
        "error": error_msg,
        "setup_url": "/auth/zoho",
        "docs_url": "https://www.zoho.com/books/api/v3/",
    }


@router.post("/revoke")
def zoho_revoke():
    """Clear stored Zoho tokens."""
    client = get_zoho_client()
    client.clear_tokens()

    return {
        "status": "revoked",
        "message": "Zoho tokens cleared. Re-authorize at /auth/zoho",
    }
