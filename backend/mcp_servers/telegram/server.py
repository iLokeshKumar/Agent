"""Telegram Bot MCP Server — REAL (no stub, no mock).

Sends messages to dealers/customers via Telegram Bot API.
Used by K3 for the telegram campaign channel.

Config via backend/.env:
  TELEGRAM_BOT_TOKEN=123456:ABC-DEF...

The dealer must have previously messaged the bot to open a chat window
(Telegram policy — bots cannot initiate unless using approved channels).
For campaign blasts, store the dealer's chat_id in CampaignContact.telegram_chat_id.
"""

import logging
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    pass


def _require_config():
    if not settings.telegram_bot_token:
        raise TelegramError(
            "Telegram not configured — set TELEGRAM_BOT_TOKEN in backend/.env"
        )


def _api_url(method: str) -> str:
    return TELEGRAM_API_BASE.format(token=settings.telegram_bot_token, method=method)


def send_message(chat_id: str, text: str, parse_mode: str = "HTML") -> dict:
    """Send a text message to a Telegram chat.

    chat_id: the dealer's Telegram chat ID (stored in CampaignContact.telegram_chat_id).
    text: message body — supports HTML tags (<b>, <i>, <a href=...>) when parse_mode='HTML'.
    Returns the Telegram message object on success.
    Raises TelegramError on failure.
    """
    _require_config()
    logger.info("Telegram send_message to chat_id=%s len=%d", chat_id, len(text))
    try:
        resp = httpx.post(
            _api_url("sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text[:4096],  # Telegram hard limit
                "parse_mode": parse_mode,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram API error: {data.get('description', data)}")
        return data.get("result", {})
    except httpx.HTTPStatusError as exc:
        raise TelegramError(
            f"Telegram HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc


def send_photo(chat_id: str, photo_url: str, caption: str = "") -> dict:
    """Send a photo with optional caption — useful for product promo images."""
    _require_config()
    try:
        resp = httpx.post(
            _api_url("sendPhoto"),
            json={
                "chat_id": chat_id,
                "photo": photo_url,
                "caption": caption[:1024],
                "parse_mode": "HTML",
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram API error: {data.get('description', data)}")
        return data.get("result", {})
    except httpx.HTTPStatusError as exc:
        raise TelegramError(
            f"Telegram HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc


def send_inline_keyboard(
    chat_id: str,
    text: str,
    buttons: list[dict],  # [{"text": "Interested", "callback_data": "interested"}]
) -> dict:
    """Send a message with inline keyboard buttons — dealer can tap to respond."""
    _require_config()
    keyboard = [[{"text": b["text"], "callback_data": b.get("callback_data", b["text"])}]
                for b in buttons]
    try:
        resp = httpx.post(
            _api_url("sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text[:4096],
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard},
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram API error: {data.get('description', data)}")
        return data.get("result", {})
    except httpx.HTTPStatusError as exc:
        raise TelegramError(
            f"Telegram HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc


def get_updates(offset: int = 0, timeout: int = 0) -> list[dict]:
    """Poll for incoming messages — used to capture dealer chat IDs on first contact."""
    _require_config()
    resp = httpx.get(
        _api_url("getUpdates"),
        params={"offset": offset, "timeout": timeout},
        timeout=timeout + 10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", [])
