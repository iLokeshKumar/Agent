"""Encrypted per-user Zoho OAuth token persistence."""
from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.user import User
from app.models.zoho_connection import ZohoOAuthState, ZohoUserConnection


def _cipher() -> Fernet:
    secret = settings.zoho_token_encryption_key
    if not secret:
        raise RuntimeError(
            "Per-user Zoho connections require ZOHO_TOKEN_ENCRYPTION_KEY. "
            "Set a strong persistent secret in the server environment."
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_refresh_token(token: str) -> str:
    return _cipher().encrypt(token.encode()).decode()


def decrypt_refresh_token(encrypted: str) -> str:
    try:
        return _cipher().decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt the Zoho refresh token. Check that "
            "ZOHO_TOKEN_ENCRYPTION_KEY has not changed."
        ) from exc


def create_user_oauth_state(user_id: str, nonce: str, expires_at: datetime) -> None:
    digest = hashlib.sha256(nonce.encode()).hexdigest()
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.id == uuid.UUID(str(user_id)), User.enabled.is_(True))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError("The Zoho connection owner is no longer enabled.")
        db.add(ZohoOAuthState(
            nonce_hash=digest,
            user_id=user.id,
            expires_at=expires_at,
        ))
        db.commit()


def consume_user_oauth_state(
    user_id: str,
    username: str,
    nonce: str,
) -> bool:
    digest = hashlib.sha256(nonce.encode()).hexdigest()
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(
                User.id == uuid.UUID(str(user_id)),
                User.username == username,
                User.enabled.is_(True),
            )
        ).scalar_one_or_none()
        row = db.get(ZohoOAuthState, digest)
        if user is None or row is None or row.user_id != user.id:
            return False
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        db.delete(row)
        db.commit()
        return expires_at > datetime.now(timezone.utc)


def get_user_zoho_connection(user_id: str) -> tuple[dict, str] | None:
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.username == user_id, User.enabled.is_(True))
        ).scalar_one_or_none()
        if user is None:
            return None
        row = db.execute(
            select(ZohoUserConnection).where(ZohoUserConnection.user_id == user.id)
        ).scalar_one_or_none()
        if row is None:
            return None
        return ({
            "refresh_token": decrypt_refresh_token(row.refresh_token_encrypted),
            "organization_id": row.organization_id,
            "data_center": row.data_center,
        }, str(user.id))


def save_user_zoho_connection(
    user_id: str,
    refresh_token: str,
    organization_id: str,
    organization_name: str | None,
    data_center: str,
) -> None:
    encrypted = encrypt_refresh_token(refresh_token)
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.id == uuid.UUID(str(user_id)), User.enabled.is_(True))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError("The user account is not available.")
        row = db.execute(
            select(ZohoUserConnection).where(ZohoUserConnection.user_id == user.id)
        ).scalar_one_or_none()
        if row is None:
            row = ZohoUserConnection(user_id=user.id)
            db.add(row)
        row.refresh_token_encrypted = encrypted
        row.organization_id = organization_id
        row.organization_name = organization_name
        row.data_center = data_center
        row.connected_at = datetime.now(timezone.utc)
        db.commit()


def update_user_zoho_refresh_token(user_id: str, refresh_token: str) -> None:
    encrypted = encrypt_refresh_token(refresh_token)
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.username == user_id, User.enabled.is_(True))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError("The Zoho connection owner is no longer enabled.")
        row = db.execute(
            select(ZohoUserConnection).where(ZohoUserConnection.user_id == user.id)
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("The user Zoho connection no longer exists.")
        row.refresh_token_encrypted = encrypted
        db.commit()


def disconnect_user_zoho(user_id: str) -> bool:
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.username == user_id, User.enabled.is_(True))
        ).scalar_one_or_none()
        if user is None:
            return False
        row = db.execute(
            select(ZohoUserConnection).where(ZohoUserConnection.user_id == user.id)
        ).scalar_one_or_none()
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True


def user_zoho_status(user_id: str) -> dict:
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.username == user_id, User.enabled.is_(True))
        ).scalar_one_or_none()
        if user is None:
            return {"connected": False}
        row = db.execute(
            select(ZohoUserConnection).where(ZohoUserConnection.user_id == user.id)
        ).scalar_one_or_none()
        if row is None:
            return {"connected": False}
        return {
            "connected": True,
            "organization_name": row.organization_name,
            "organization_id": row.organization_id,
            "data_center": row.data_center,
            "connected_at": row.connected_at.isoformat() if row.connected_at else None,
        }
