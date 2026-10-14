"""WhatsApp Web (Playwright) bridge — API endpoints.

GET  /mcp/whatsapp-web/status     — is the browser logged in?
GET  /mcp/whatsapp-web/qr         — redirect to QR scan page on port 8002
POST /mcp/whatsapp-web/send       — send a message via browser automation

This calls the Playwright service running on port 8002.
Start it with:  python -m mcp.whatsapp_web.server
"""

import os
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

router = APIRouter(prefix="/mcp/whatsapp-web", tags=["mcp-whatsapp-web"])

WA_WEB_URL = os.environ.get("WA_WEB_URL", "http://localhost:8002")
_client = httpx.Client(timeout=30)


def _call(method: str, path: str, **kwargs):
    try:
        resp = _client.request(method, f"{WA_WEB_URL}{path}", **kwargs)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=(
                "WhatsApp Web service is not running. "
                "Start it with: python -m mcp.whatsapp_web.server"
            ),
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)


@router.get("/status")
def status():
    return _call("GET", "/status")


@router.get("/qr")
def qr():
    """Redirect browser to the QR scan page served by the Playwright process."""
    return RedirectResponse(url=f"{WA_WEB_URL}/qr")


class SendRequest(BaseModel):
    to: str
    message: str
    is_group: bool = False


@router.post("/send")
def send(req: SendRequest):
    return _call("POST", "/send", json=req.model_dump())


@router.get("/screenshot")
def screenshot():
    """Debug: returns a base64 screenshot of the browser."""
    return _call("GET", "/screenshot")
