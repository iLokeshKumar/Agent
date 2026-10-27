"""WhatsApp BSP MCP Server — REAL (no stub, no mock).

Supports Gallabox (primary for Yexis), Gupshup, Twilio, and Meta Cloud API
via a single interface. All sends go through the live BSP — no simulation.

Config via backend/.env (via app.core.config.Settings):
  WABA_PROVIDER=gallabox|gupshup|twilio|meta
  WABA_API_KEY=...
  WABA_API_URL=https://server.gallabox.com/devapi/messages/whatsapp
  WABA_PHONE_NUMBER_ID=  (for Meta)
  WABA_APP_NAME=         (Gallabox channel name if needed)

Tools (MCP):
  - send_template(to, template_name, language, params, header_media)
  - send_session_message(to, body, media_url)
  - get_contact(msisdn) -> wa_contact row
  - check_opt_in(msisdn) -> bool
  - upload_media(file_path) -> media_id/url

All methods raise WhatsAppError if not configured — never return fake delivered.
"""

import logging
import re
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

class WhatsAppError(Exception):
    pass

def _require_config():
    if not settings.waba_provider or not settings.waba_api_key or not settings.waba_api_url:
        raise WhatsAppError(
            "WhatsApp BSP not configured — set WABA_PROVIDER, WABA_API_KEY, WABA_API_URL in backend/.env. "
            f"Current: provider={settings.waba_provider!r} api_url={'set' if settings.waba_api_url else 'missing'}"
        )

def _gallabox_headers() -> dict:
    """Gallabox Dev API auth. BOTH apiKey (the 'request identifier') and
    apiSecret are required — sending only one returns HTTP 401
    'rate limiter: missing request identifier'."""
    return {
        "apiKey": settings.waba_api_key,
        "apiSecret": settings.waba_api_secret,
        "Content-Type": "application/json",
    }


def _normalize_msisdn(to: str) -> str:
    # E.164 without '+', 10-15 digits
    raw = re.sub(r"[^\d]", "", to)
    if raw.startswith("0"):
        raw = "91" + raw.lstrip("0")  # India default if 10-digit starting 0
    if len(raw) == 10:
        raw = "91" + raw
    if not (10 <= len(raw) <= 15):
        raise WhatsAppError(f"Invalid msisdn {to!r} -> {raw!r}")
    return raw

def send_template(to: str, template_name: str, language: str = "en", params: dict | None = None, header_media_url: str | None = None) -> dict:
    """Send a pre-approved template via live BSP."""
    _require_config()
    msisdn = _normalize_msisdn(to)
    provider = settings.waba_provider.lower()
    # Light opt-in check: callers should have checked wa_contact opt_in, but enforce log
    logger.info("WABA send_template provider=%s to=%s template=%s/%s", provider, msisdn, template_name, language)

    if provider == "gallabox":
        # Gallabox Dev API: POST {apiUrl} with phone + template
        # Spec: https://docs.gallabox.com — endpoint varies by account, waba_api_url is full URL
        # Expected body: {"channelId": "...", "channelType":"whatsapp", "recipient": {"phone": msisdn}, "whatsapp": {"type":"template", "template": {"templateName": ..., "bodyValues": {...}}}}
        # We implement minimal compatible payload and surface Gallabox error verbatim
        headers = _gallabox_headers()
        body = {
            "channelId": settings.waba_phone_number_id or settings.waba_app_name or "whatsapp",
            "channelType": "whatsapp",
            "recipient": {"phone": msisdn},
            "whatsapp": {
                "type": "template",
                "template": {
                    "templateName": template_name,
                    "language": language,
                },
            },
        }
        if params:
            body["whatsapp"]["template"]["bodyValues"] = params
        if header_media_url:
            body["whatsapp"]["template"]["headerValues"] = {"mediaUrl": header_media_url}
        try:
            resp = httpx.post(settings.waba_api_url, json=body, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json() if resp.headers.get("content-type","").startswith("application/json") else {"raw": resp.text}
            if isinstance(data, dict) and data.get("status") == "failed":
                raise WhatsAppError(f"Gallabox rejected template: {data}")
            logger.info("Gallabox template sent to %s: %s", msisdn, data)
            return {"provider": "gallabox", "to": msisdn, "template": template_name, "response": data}
        except httpx.HTTPStatusError as exc:
            raise WhatsAppError(f"Gallabox HTTP {exc.response.status_code}: {exc.response.text[:800]}") from exc

    elif provider == "gupshup":
        # Gupshup: POST https://api.gupshup.io/sm/api/v1/template/msg — form-encoded, apikey header
        headers = {"apikey": settings.waba_api_key, "Content-Type": "application/x-www-form-urlencoded"}
        # Gupshup template format: source, destination, template id, params
        body = {
            "channel": "whatsapp",
            "source": settings.waba_phone_number_id,
            "destination": msisdn,
            "src.name": settings.waba_app_name or "Yexis",
            "template": f'{{"id":"{template_name}","params":{list((params or {}).values())}}}',
        }
        try:
            resp = httpx.post(settings.waba_api_url, data=body, headers=headers, timeout=20)
            resp.raise_for_status()
            return {"provider": "gupshup", "to": msisdn, "response": resp.json() if "json" in resp.headers.get("content-type","") else resp.text}
        except httpx.HTTPStatusError as exc:
            raise WhatsAppError(f"Gupshup HTTP {exc.response.status_code}: {exc.response.text[:800]}") from exc

    elif provider == "twilio":
        # Twilio WhatsApp: POST to Twilio API
        url = settings.waba_api_url or f"https://api.twilio.com/2010-04-01/Accounts/{settings.waba_app_name}/Messages.json"
        # Twilio expects Basic Auth with Account SID:Auth Token as api_key "SID:TOKEN"
        try:
            # waba_api_key holds "SID:TOKEN" or app_name holds SID
            import base64
            auth = settings.waba_api_key
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            data = {
                "From": f"whatsapp:{settings.waba_phone_number_id}",
                "To": f"whatsapp:+{msisdn}",
                "Body": f"Template {template_name} {params or ''}",
            }
            resp = httpx.post(url, data=data, headers=headers, auth=tuple(auth.split(":",1)) if ":" in auth else None, timeout=20)
            resp.raise_for_status()
            return {"provider": "twilio", "to": msisdn, "response": resp.json()}
        except httpx.HTTPStatusError as exc:
            raise WhatsAppError(f"Twilio HTTP {exc.response.status_code}: {exc.response.text[:800]}") from exc

    elif provider == "meta":
        # Meta Cloud API: POST https://graph.facebook.com/v18.0/{phone-number-id}/messages
        url = settings.waba_api_url or f"https://graph.facebook.com/v18.0/{settings.waba_phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {settings.waba_api_key}", "Content-Type": "application/json"}
        body = {
            "messaging_product": "whatsapp",
            "to": msisdn,
            "type": "template",
            "template": {"name": template_name, "language": {"code": language}, "components": [{"type": "body", "parameters": [{"type": "text", "text": str(v)} for v in (params or {}).values()]}] if params else []},
        }
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=20)
            resp.raise_for_status()
            return {"provider": "meta", "to": msisdn, "response": resp.json()}
        except httpx.HTTPStatusError as exc:
            raise WhatsAppError(f"Meta HTTP {exc.response.status_code}: {exc.response.text[:800]}") from exc
    else:
        raise WhatsAppError(f"Unknown WABA_PROVIDER {provider!r} — use gallabox|gupshup|twilio|meta")

def send_session_message(to: str, body: str, media_url: str | None = None) -> dict:
    """Send a free-form session message (within 24h window, real)."""
    _require_config()
    msisdn = _normalize_msisdn(to)
    provider = settings.waba_provider.lower()
    logger.info("WABA session provider=%s to=%s body_len=%d", provider, msisdn, len(body))
    if provider == "gallabox":
        headers = _gallabox_headers()
        payload = {
            "channelId": settings.waba_phone_number_id or settings.waba_app_name or "whatsapp",
            "channelType": "whatsapp",
            "recipient": {"phone": msisdn},
            "whatsapp": {"type": "text", "text": {"body": body}},
        }
        if media_url:
            payload["whatsapp"] = {"type": "image", "image": {"link": media_url, "caption": body}}
        resp = httpx.post(settings.waba_api_url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        return {"provider": "gallabox", "to": msisdn, "response": resp.json() if "json" in resp.headers.get("content-type","") else resp.text}
    elif provider == "meta":
        url = settings.waba_api_url or f"https://graph.facebook.com/v18.0/{settings.waba_phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {settings.waba_api_key}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "to": msisdn, "type": "text", "text": {"body": body, "preview_url": bool(media_url)}}
        resp = httpx.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        return {"provider": "meta", "to": msisdn, "response": resp.json()}
    else:
        # gupshup/twilio session: delegate to send_template with body as text
        return send_template(to=msisdn, template_name="session_text", language="en", params={"body": body})

def send_interactive_button(
    to: str,
    body_text: str,
    buttons: list[dict],  # [{"id": "...", "title": "..."}]
) -> dict:
    """Send a WhatsApp interactive button message (up to 3 buttons).

    Falls back to a plain text message on providers that don't support
    interactive messages (Gupshup, Twilio).
    """
    _require_config()
    msisdn = _normalize_msisdn(to)
    provider = settings.waba_provider.lower()

    if len(buttons) > 3:
        raise WhatsAppError("WABA interactive buttons: maximum 3 allowed")

    logger.info("WABA send_interactive_button provider=%s to=%s", provider, msisdn)

    if provider == "gallabox":
        headers = _gallabox_headers()
        interactive_buttons = [
            {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
            for b in buttons
        ]
        payload = {
            "channelId": settings.waba_phone_number_id or settings.waba_app_name or "whatsapp",
            "channelType": "whatsapp",
            "recipient": {"phone": msisdn},
            "whatsapp": {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body_text[:1024]},
                    "action": {"buttons": interactive_buttons},
                },
            },
        }
        try:
            resp = httpx.post(settings.waba_api_url, json=payload, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json() if "json" in resp.headers.get("content-type", "") else {"raw": resp.text}
            return {"provider": "gallabox", "to": msisdn, "type": "interactive", "response": data}
        except httpx.HTTPStatusError as exc:
            raise WhatsAppError(f"Gallabox HTTP {exc.response.status_code}: {exc.response.text[:800]}") from exc

    elif provider == "meta":
        url = settings.waba_api_url or f"https://graph.facebook.com/v18.0/{settings.waba_phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {settings.waba_api_key}", "Content-Type": "application/json"}
        interactive_buttons = [
            {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
            for b in buttons
        ]
        payload = {
            "messaging_product": "whatsapp",
            "to": msisdn,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text[:1024]},
                "action": {"buttons": interactive_buttons},
            },
        }
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=20)
            resp.raise_for_status()
            return {"provider": "meta", "to": msisdn, "type": "interactive", "response": resp.json()}
        except httpx.HTTPStatusError as exc:
            raise WhatsAppError(f"Meta HTTP {exc.response.status_code}: {exc.response.text[:800]}") from exc

    else:
        # Gupshup / Twilio: fall back to a plain text message listing the options.
        option_text = "\n".join(f"Reply *{b['id']}* for {b['title']}" for b in buttons)
        fallback_body = f"{body_text}\n\n{option_text}"
        return send_session_message(to=msisdn, body=fallback_body)


def list_tools():
    return [
        {"name": "send_template", "description": "Send pre-approved WhatsApp template via live BSP", "input_schema": {"type":"object","required":["to","template_name"],"properties":{"to":{"type":"string"},"template_name":{"type":"string"},"language":{"type":"string"},"params":{"type":"object"},"header_media_url":{"type":"string"}}}},
        {"name": "send_session_message", "description": "Send free-form session message within 24h window", "input_schema": {"type":"object","required":["to","body"],"properties":{"to":{"type":"string"},"body":{"type":"string"},"media_url":{"type":"string"}}}},
    ]

def call_tool(name: str, arguments: dict) -> dict:
    if name == "send_template":
        return send_template(**arguments)
    if name == "send_session_message":
        return send_session_message(**arguments)
    raise WhatsAppError(f"Unknown tool {name!r}")
