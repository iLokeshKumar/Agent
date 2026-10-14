"""Seed configured admin and the automation service identity.

Creates a single 'admin' user with no agent-level restrictions.
Because user_agent_permissions is empty, the opt-in model allows
this user to call all agents.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User


# Admin identity is created only when a password is explicitly configured.
DEFAULT_USERS = [
    {
        "username": settings.admin_username or "admin",
        "display_name": "Platform Admin",
        "role": "admin",
        "email": settings.admin_email or None,
        "email_verified": True,
        "password": settings.admin_password or None,
    },
    {
        # Service identity for Automation Gateway submissions
        # (webhooks + scheduled lanes). A distinct, auditable actor —
        # never a human's account.
        "username": "gateway",
        "display_name": "Automation Gateway (service)",
        "role": "system",
    },
]


def _hash_password(plain: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        import hashlib
        return "sha256:" + hashlib.sha256(plain.encode()).hexdigest()


def _password_matches(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        import bcrypt
        if hashed.startswith("sha256:"):
            raise ImportError
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ImportError:
        import hashlib
        return hashed == "sha256:" + hashlib.sha256(plain.encode()).hexdigest()


def seed_users(db: Session) -> None:
    now = datetime.now(timezone.utc)
    created = 0

    for item in DEFAULT_USERS:
        if item.get("role") == "admin" and not item.get("password"):
            continue
        # Match the existing account by EMAIL first (so rotating ADMIN_USERNAME
        # renames the same row instead of inserting a duplicate email), then by
        # username. This is what makes the .env-driven admin identity rotatable.
        existing = None
        if item.get("email"):
            existing = db.query(User).filter(User.email == item["email"]).first()
        if existing is None:
            existing = db.query(User).filter(User.username == item["username"]).first()

        if existing is None:
            pw_hash = _hash_password(item["password"]) if item.get("password") else None
            db.add(User(
                id=uuid.uuid4(),
                username=item["username"],
                display_name=item["display_name"],
                role=item.get("role", "viewer"),
                email=item.get("email"),
                email_verified=bool(item.get("email_verified", False)),
                password_hash=pw_hash,
                enabled=True,
                created_at=now,
            ))
            created += 1
        else:
            # Rotate identity in place from .env. Only rename the username if the
            # new one isn't taken by a different row (avoids a unique clash).
            if existing.username != item["username"]:
                clash = (
                    db.query(User)
                    .filter(User.username == item["username"], User.id != existing.id)
                    .first()
                )
                if clash is None:
                    existing.username = item["username"]
            if item.get("display_name"):
                existing.display_name = item["display_name"]
            existing.role = item.get("role", existing.role or "viewer")
            if item.get("email"):
                existing.email = item["email"]
            if item.get("email_verified"):
                existing.email_verified = True
            # The .env password is authoritative for the bootstrap admin: if the
            # stored hash doesn't already match ADMIN_PASSWORD, refresh it so the
            # value in .env always works. (Set ADMIN_PASSWORD to rotate it.)
            if item.get("password") and not _password_matches(item["password"], existing.password_hash):
                existing.password_hash = _hash_password(item["password"])

    db.commit()

    if created:
        print(f"Seeded {created} user(s)")
    else:
        print("Users already seeded — skipped")
