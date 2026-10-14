"""Unified WhatsApp client for use by skills/agents.

Automatically routes to whichever backend is configured:
  WABA_PROVIDER set  → WABA API (Gallabox/Gupshup/Meta/Twilio)
  WA_WEB_ENABLED=true → Playwright WhatsApp Web (port 8002)

Usage in any skill:
    from mcp.whatsapp.client import send_whatsapp
    send_whatsapp(to="+919876543210", message="Your PO is approved.")
"""

import logging
import os

logger = logging.getLogger(__name__)

WA_WEB_ENABLED = os.environ.get("WA_WEB_ENABLED", "false").lower() == "true"
WA_WEB_URL = os.environ.get("WA_WEB_URL", "http://localhost:8002")


def send_whatsapp(to: str, message: str, template_name: str | None = None,
                  template_params: dict | None = None) -> dict:
    """Send a WhatsApp message using whichever backend is active.

    Args:
        to:               Recipient — E.164 phone number or contact name.
        message:          Free-form text (used for session messages and WA Web).
        template_name:    If set and using WABA API, sends as template instead.
        template_params:  Template variable values.

    Returns dict with {"status": "sent", "provider": "waba|whatsapp_web", ...}
    Raises RuntimeError if neither backend is configured.
    """
    from app.core.config import settings

    # --- WABA API (preferred) ---
    if settings.waba_provider and settings.waba_api_key and settings.waba_api_url:
        from mcp_servers.whatsapp.server import send_session_message, send_template, WhatsAppError
        try:
            if template_name:
                result = send_template(
                    to=to,
                    template_name=template_name,
                    params=template_params,
                )
            else:
                result = send_session_message(to=to, body=message)
            return {"provider": "waba", **result}
        except WhatsAppError as exc:
            logger.error("WABA send failed: %s — falling back to WA Web if enabled", exc)
            if not WA_WEB_ENABLED:
                raise RuntimeError(f"WABA send failed: {exc}") from exc

    # --- WhatsApp Web / Playwright fallback ---
    if WA_WEB_ENABLED:
        import httpx
        try:
            resp = httpx.post(
                f"{WA_WEB_URL}/send",
                json={"to": to, "message": message},
                timeout=30,
            )
            resp.raise_for_status()
            return {"provider": "whatsapp_web", **resp.json()}
        except httpx.ConnectError as exc:
            raise RuntimeError(
                "WhatsApp Web service not running. "
                "Start: python -m mcp_servers.whatsapp_web.server"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"WA Web send failed: {exc.response.text}") from exc

    raise RuntimeError(
        "No WhatsApp backend configured.\n"
        "Option A (WABA API): set WABA_PROVIDER + WABA_API_KEY + WABA_API_URL in .env\n"
        "Option B (WA Web):   set WA_WEB_ENABLED=true in .env and run: "
        "python -m mcp_servers.whatsapp_web.server"
    )


def whatsapp_configured() -> dict:
    """Return which backends are available."""
    from app.core.config import settings
    return {
        "waba_api": bool(settings.waba_provider and settings.waba_api_key and settings.waba_api_url),
        "waba_provider": settings.waba_provider or None,
        "whatsapp_web": WA_WEB_ENABLED,
        "whatsapp_web_url": WA_WEB_URL if WA_WEB_ENABLED else None,
    }
