"""Authentication headers for gateway-to-backend service requests."""

from app.core.config import settings


def task_submission_headers() -> dict[str, str]:
    if not settings.gateway_api_token:
        return {}
    return {"Authorization": f"Bearer {settings.gateway_api_token}"}
