"""JWT-based session auth for the control-room UI.

Token is stored in an httpOnly cookie 'platform_session'.
API clients may also pass Bearer <token> in Authorization header.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import settings

COOKIE_NAME = "platform_session"
ALGORITHM = "HS256"

# Canonical roles and the UI sections each may access.
BUILT_IN_ROLES: list[str] = ["admin", "finance", "gsm", "purchase", "ops", "viewer"]

DEFAULT_ROLE_SECTIONS: dict[str, set[str]] = {
    "admin":    {"demo", "overview", "tasks", "agents", "approvals",
                 "campaigns", "templates", "optouts", "lanes", "tally",
                 "mail", "users", "activity", "integrations"},
    "finance":  {"demo", "overview", "tasks", "approvals",
                 "tally", "mail", "activity", "integrations"},
    "gsm":      {"demo", "overview", "campaigns", "templates",
                 "optouts", "activity", "integrations"},
    "purchase": {"demo", "overview", "tasks", "approvals", "activity", "integrations"},
    "ops":      {"overview", "tasks", "agents", "lanes", "activity", "integrations"},
    "viewer":   {"overview", "integrations"},
    "system":   set(),
}

# Demo scenarios each role may trigger
DEFAULT_DEMO_SCENARIOS: dict[str, set[str]] = {
    "admin":    {"f1_collections", "f3_books_tally", "m2_gsm_brief", "p1_purchase"},
    "finance":  {"f1_collections", "f3_books_tally"},
    "gsm":      {"m2_gsm_brief"},
    "purchase": {"p1_purchase"},
    "ops":      set(),
    "viewer":   set(),
    "system":   set(),
}

PERMISSION_SECTIONS = (
    "overview", "tasks", "agents", "approvals", "campaigns", "templates",
    "optouts", "lanes", "tally", "mail", "integrations", "users", "activity",
    "demo", "demo:f1_collections", "demo:f3_books_tally",
    "demo:m2_gsm_brief", "demo:p1_purchase",
)
# Compatibility aliases for older modules; runtime checks use database grants.
ROLES = BUILT_IN_ROLES
ROLE_SECTIONS = DEFAULT_ROLE_SECTIONS
ROLE_DEMO_SCENARIOS = DEFAULT_DEMO_SCENARIOS


def _secret() -> str:
    s = settings.jwt_secret
    if not s:
        # Only acceptable in development; production must set JWT_SECRET in .env
        import warnings
        warnings.warn("JWT_SECRET not set — using insecure default", stacklevel=3)
        return "dev-insecure-secret-CHANGE-IN-PRODUCTION"
    return s


def create_token(user_id: str, username: str, role: str) -> str:
    payload = {
        "sub": username,
        "uid": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.PyJWTError:
        return None


class CurrentUser:
    def __init__(
        self,
        user_id: str,
        username: str,
        role: str,
        grants: frozenset[tuple[str, str]] | None = None,
    ):
        self.user_id = user_id
        self.username = username
        self.role = role
        self.grants = grants

    def can(self, section: str, action: str = "view") -> bool:
        if self.role == "admin":
            return True
        if self.grants is not None:
            return (section, action) in self.grants
        return action == "view" and section in DEFAULT_ROLE_SECTIONS.get(self.role, set())

    def can_run_demo(self, scenario_id: str) -> bool:
        section = f"demo:{scenario_id}"
        if self.grants is not None:
            return (
                ("demo", "view") in self.grants
                and (section, "run") in self.grants
            )
        return scenario_id in DEFAULT_DEMO_SCENARIOS.get(self.role, set())


def get_current_user(request: Request) -> Optional[CurrentUser]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    user_id = payload.get("uid", "")
    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return None
    from app.core.db import SessionLocal
    from app.models.access_control import RolePermission
    from app.models.user import User
    from sqlalchemy import select
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.id == user_uuid, User.enabled.is_(True))
        ).scalar_one_or_none()
        if user is None:
            return None
        grants = frozenset(db.execute(
            select(RolePermission.section, RolePermission.action)
            .where(RolePermission.role == user.role)
        ).all())
        return CurrentUser(
            user_id=str(user.id),
            username=user.username,
            role=user.role or "viewer",
            grants=grants,
        )


def log_activity(
    db: Session,
    user: CurrentUser | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
) -> None:
    from app.models.activity_log import ActivityLog
    entry = ActivityLog(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user.user_id) if (user and user.user_id) else None,
        username=user.username if user else "system",
        role=user.role if user else "system",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        ip_address=ip,
        ts=datetime.now(timezone.utc),
    )
    db.add(entry)
    db.commit()
