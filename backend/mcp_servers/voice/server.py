"""Voice / IVR Campaign MCP Server — REAL (no stub, no mock).

Supports Exotel (primary for India), Twilio Voice, and Ozonetel.
Sends outbound voice calls with TTS or pre-recorded audio messages.

Config via backend/.env:
  VOICE_PROVIDER=exotel|twilio|ozonetel
  VOICE_API_KEY=SID:TOKEN   (Exotel/Twilio format — colon-separated)
  VOICE_API_URL=            (optional endpoint override)
  VOICE_CALLER_ID=          (outbound number, e.g. 08039XXXXXX)
  VOICE_TTS_LANGUAGE=en-IN  (Exotel TTS language code)

Exotel: https://developer.exotel.com/api/
Twilio: https://www.twilio.com/docs/voice/api/call-resource
Ozonetel: https://ozonetel.com/api/
"""

import logging
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class VoiceError(Exception):
    pass


def _require_config():
    if not settings.voice_provider or not settings.voice_api_key:
        raise VoiceError(
            "Voice not configured — set VOICE_PROVIDER, VOICE_API_KEY, "
            "VOICE_CALLER_ID in backend/.env. "
            f"Current: provider={settings.voice_provider!r}"
        )


def _normalize_msisdn(to: str) -> str:
    import re
    raw = re.sub(r"[^\d]", "", to)
    if raw.startswith("0"):
        raw = "91" + raw.lstrip("0")
    if len(raw) == 10:
        raw = "91" + raw
    return raw


def make_call(to: str, tts_text: str | None = None, audio_url: str | None = None) -> dict:
    """Place an outbound voice call with TTS message or pre-recorded audio.

    to: destination phone number (E.164 or local India format)
    tts_text: text to speak via TTS (if provider supports it)
    audio_url: URL of pre-recorded MP3/WAV to play (alternative to TTS)

    At least one of tts_text or audio_url must be provided.
    Returns provider response dict on success.
    Raises VoiceError on configuration or API failure.
    """
    _require_config()
    if not tts_text and not audio_url:
        raise VoiceError("Provide tts_text or audio_url for the voice call")

    msisdn = _normalize_msisdn(to)
    provider = settings.voice_provider.lower()
    logger.info("Voice make_call provider=%s to=%s", provider, msisdn)

    if provider == "exotel":
        return _exotel_call(msisdn, tts_text, audio_url)
    elif provider == "twilio":
        return _twilio_call(msisdn, tts_text, audio_url)
    elif provider == "ozonetel":
        return _ozonetel_call(msisdn, tts_text, audio_url)
    else:
        raise VoiceError(f"Unknown VOICE_PROVIDER {provider!r} — use exotel|twilio|ozonetel")


def _exotel_call(msisdn: str, tts_text: str | None, audio_url: str | None) -> dict:
    """Exotel outbound call.

    Exotel Call API: POST https://api.exotel.com/v1/Accounts/{SID}/Calls/connect
    Auth: Basic (SID, token)
    """
    creds = settings.voice_api_key.split(":", 1)
    if len(creds) != 2:
        raise VoiceError("VOICE_API_KEY for Exotel must be 'AccountSID:AuthToken'")
    sid, token = creds

    url = (
        settings.voice_api_url
        or f"https://api.exotel.com/v1/Accounts/{sid}/Calls/connect"
    )

    data: dict = {
        "From": msisdn,
        "CallerId": settings.voice_caller_id,
        "StatusCallback": "",
    }

    if audio_url:
        # Exotel plays a pre-recorded audio file from URL
        data["Url"] = audio_url
    elif tts_text:
        # Exotel TTS: wrap text in Exotel XML (ExoML)
        lang = settings.voice_tts_language or "en-IN"
        data["Url"] = (
            f"http://my.exotel.com/exoml/start_voice/"
            f"?text={tts_text}&language={lang}"
        )
        # Alternate: use inline ExoML via body parameter when supported
        data["Body"] = tts_text
        data["Language"] = lang

    try:
        resp = httpx.post(url, data=data, auth=(sid, token), timeout=20)
        resp.raise_for_status()
        result = resp.json() if "json" in resp.headers.get("content-type", "") else {"raw": resp.text}
        return {"provider": "exotel", "to": msisdn, "response": result}
    except httpx.HTTPStatusError as exc:
        raise VoiceError(
            f"Exotel HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc


def _twilio_call(msisdn: str, tts_text: str | None, audio_url: str | None) -> dict:
    """Twilio outbound call with TwiML."""
    creds = settings.voice_api_key.split(":", 1)
    if len(creds) != 2:
        raise VoiceError("VOICE_API_KEY for Twilio must be 'AccountSID:AuthToken'")
    sid, token = creds

    url = (
        settings.voice_api_url
        or f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    )

    if tts_text:
        twiml = f"<Response><Say language='{settings.voice_tts_language or 'en-IN'}'>{tts_text}</Say></Response>"
    elif audio_url:
        twiml = f"<Response><Play>{audio_url}</Play></Response>"
    else:
        twiml = "<Response><Say>Hello from Yexis Electronics.</Say></Response>"

    data = {
        "To": f"+{msisdn}",
        "From": settings.voice_caller_id,
        "Twiml": twiml,
    }

    try:
        resp = httpx.post(url, data=data, auth=(sid, token), timeout=20)
        resp.raise_for_status()
        return {"provider": "twilio", "to": msisdn, "response": resp.json()}
    except httpx.HTTPStatusError as exc:
        raise VoiceError(
            f"Twilio HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc


def _ozonetel_call(msisdn: str, tts_text: str | None, audio_url: str | None) -> dict:
    """Ozonetel outbound call via Click-to-Call API."""
    url = settings.voice_api_url or "https://in1-ccaas.ozonetel.com/crm/api/click-to-call"
    params = {
        "api_key": settings.voice_api_key,
        "customer_number": msisdn,
        "agent_number": settings.voice_caller_id,
    }
    if tts_text:
        params["tts"] = tts_text
    if audio_url:
        params["audio_url"] = audio_url

    try:
        resp = httpx.get(url, params=params, timeout=20)
        resp.raise_for_status()
        result = resp.json() if "json" in resp.headers.get("content-type", "") else {"raw": resp.text}
        return {"provider": "ozonetel", "to": msisdn, "response": result}
    except httpx.HTTPStatusError as exc:
        raise VoiceError(
            f"Ozonetel HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc
