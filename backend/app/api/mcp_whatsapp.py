"""WhatsApp BSP MCP — real, no mock.

Tools:
  POST /mcp/whatsapp/send_template
  POST /mcp/whatsapp/send_message
  GET  /mcp/whatsapp/health

Every call hits live BSP (Gallabox/Gupshup/Twilio/Meta) via mcp/whatsapp/server.py.
If not configured → 400 with clear instructions, not fake delivered.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mcp_servers.whatsapp.server import WhatsAppError, send_session_message, send_template

router = APIRouter(prefix="/mcp/whatsapp", tags=["mcp-whatsapp"])

class TemplateRequest(BaseModel):
    to: str
    template_name: str
    language: str = "en"
    params: dict | None = None
    header_media_url: str | None = None

class SessionRequest(BaseModel):
    to: str
    body: str
    media_url: str | None = None

@router.get("/health")
def health():
    from app.core.config import settings
    return {
        "provider": settings.waba_provider or "not_configured",
        "api_url_set": bool(settings.waba_api_url),
        "api_key_set": bool(settings.waba_api_key),
        "phone_number_id_set": bool(settings.waba_phone_number_id),
    }

@router.post("/send_template")
def send_template_route(req: TemplateRequest):
    try:
        return send_template(to=req.to, template_name=req.template_name, language=req.language, params=req.params, header_media_url=req.header_media_url)
    except WhatsAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/send_message")
def send_message_route(req: SessionRequest):
    try:
        return send_session_message(to=req.to, body=req.body, media_url=req.media_url)
    except WhatsAppError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
