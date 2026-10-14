"""Firebase Cloud Messaging (FCM) integration for push notifications.

Supports both FCM Legacy HTTP API (server key) and FCM v1 HTTP API
(service account / OAuth2). Used by K3 for the 'push' campaign channel.

Config via backend/.env:
  FCM_PROJECT_ID=your-gcp-project-id
  FCM_SERVER_KEY=AAAAxxxxxxxx   (Legacy API key — simpler setup)
  FCM_SERVICE_ACCOUNT_JSON=     (Base64 JSON for v1 API — recommended for prod)
"""

import base64
import json
import logging
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

FCM_LEGACY_URL = "https://fcm.googleapis.com/fcm/send"
FCM_V1_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"


class FcmError(Exception):
    pass


def _require_config():
    if not settings.fcm_server_key and not settings.fcm_service_account_json:
        raise FcmError(
            "FCM not configured — set FCM_SERVER_KEY (legacy) or "
            "FCM_SERVICE_ACCOUNT_JSON + FCM_PROJECT_ID (v1 API) in backend/.env"
        )


def _get_v1_access_token() -> str:
    """Obtain a short-lived OAuth2 access token from a service account JSON."""
    import time
    import hmac
    import hashlib

    sa_json_b64 = settings.fcm_service_account_json
    if not sa_json_b64:
        raise FcmError("FCM_SERVICE_ACCOUNT_JSON not set")

    try:
        sa = json.loads(base64.b64decode(sa_json_b64))
    except Exception as exc:
        raise FcmError(f"FCM_SERVICE_ACCOUNT_JSON decode failed: {exc}") from exc

    # Build JWT for Google OAuth2
    # Header
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    # Claim
    now = int(time.time())
    claim = base64.urlsafe_b64encode(json.dumps({
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/firebase.messaging",
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600,
        "iat": now,
    }).encode()).rstrip(b"=").decode()

    # Sign with RSA private key using cryptography library
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key = serialization.load_pem_private_key(
            sa["private_key"].encode(), password=None
        )
        signature = private_key.sign(
            f"{header}.{claim}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        jwt = f"{header}.{claim}.{sig_b64}"
    except ImportError:
        raise FcmError("cryptography package required for FCM v1 API — pip install cryptography")

    # Exchange JWT for access token
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def send_push(
    fcm_token: str,
    title: str,
    body: str,
    data: dict | None = None,
    image_url: str | None = None,
) -> dict:
    """Send a push notification to a single device.

    fcm_token: the device registration token stored in CampaignContact.fcm_token.
    title: notification title (shown in system tray).
    body: notification body text.
    data: optional key-value pairs for in-app handling.
    image_url: optional image URL for rich notifications.

    Uses v1 API if FCM_SERVICE_ACCOUNT_JSON is set, otherwise Legacy API.
    Raises FcmError on configuration or API failure.
    """
    _require_config()
    logger.info("FCM push to token %s... title=%r", fcm_token[:12], title)

    if settings.fcm_service_account_json and settings.fcm_project_id:
        return _send_v1(fcm_token, title, body, data, image_url)
    else:
        return _send_legacy(fcm_token, title, body, data, image_url)


def _send_legacy(
    fcm_token: str, title: str, body: str,
    data: dict | None, image_url: str | None
) -> dict:
    """FCM Legacy HTTP API."""
    payload: dict = {
        "to": fcm_token,
        "notification": {"title": title, "body": body},
    }
    if image_url:
        payload["notification"]["image"] = image_url
    if data:
        payload["data"] = data

    try:
        resp = httpx.post(
            FCM_LEGACY_URL,
            json=payload,
            headers={
                "Authorization": f"key={settings.fcm_server_key}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("failure", 0) > 0:
            raise FcmError(f"FCM delivery failed: {result}")
        return {"provider": "fcm_legacy", "token_prefix": fcm_token[:12], "response": result}
    except httpx.HTTPStatusError as exc:
        raise FcmError(
            f"FCM Legacy HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc


def _send_v1(
    fcm_token: str, title: str, body: str,
    data: dict | None, image_url: str | None
) -> dict:
    """FCM v1 HTTP API — recommended for production."""
    access_token = _get_v1_access_token()
    url = FCM_V1_URL.format(project_id=settings.fcm_project_id)

    message: dict = {
        "token": fcm_token,
        "notification": {"title": title, "body": body},
    }
    if image_url:
        message["notification"]["image"] = image_url
    if data:
        message["data"] = {k: str(v) for k, v in data.items()}

    try:
        resp = httpx.post(
            url,
            json={"message": message},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return {"provider": "fcm_v1", "token_prefix": fcm_token[:12], "response": resp.json()}
    except httpx.HTTPStatusError as exc:
        raise FcmError(
            f"FCM v1 HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc
