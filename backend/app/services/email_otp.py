"""Email-ownership verification: 6-digit code + verification link.

On request we create a single EmailVerification row per user holding a hashed
6-digit code and a URL-safe link token, both sharing one expiry. The user can
either type the code on the verify page or click the link in the email — either
proves ownership and flips User.email_verified to True.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import EmailVerification, User

logger = logging.getLogger(__name__)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _verify_link(token: str) -> str:
    base = (settings.approval_base_url or "http://localhost:8000").rstrip("/")
    return f"{base}/auth/verify-link?token={token}"


def generate_and_send(db: Session, user: User) -> dict:
    """Create a fresh code+link for `user` and email both. Returns metadata
    (never the code itself). Requires SMTP to be configured/enabled."""
    if not user.email:
        raise ValueError("User has no email address to verify.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    link_token = secrets.token_urlsafe(32)
    ttl = max(5, int(settings.email_verification_ttl_minutes or 30))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl)

    # One active row per user — clear any previous pending codes.
    db.query(EmailVerification).filter(
        EmailVerification.user_id == user.id
    ).delete(synchronize_session=False)
    db.add(EmailVerification(
        user_id=user.id,
        email=user.email,
        code_hash=_hash_code(code),
        link_token=link_token,
        expires_at=expires_at,
    ))
    db.commit()

    _send_email(user.email, user.display_name or user.username, code, _verify_link(link_token), ttl)
    return {"sent_to": user.email, "expires_in_minutes": ttl}


def verify_code(db: Session, email: str, code: str) -> User | None:
    """Verify by typed 6-digit code. Returns the User on success, else None."""
    row = (
        db.query(EmailVerification)
        .filter(EmailVerification.email == email.strip().lower())
        .order_by(EmailVerification.created_at.desc())
        .first()
    )
    if row is None:
        return None
    if _expired(row):
        return None
    row.attempts += 1
    db.commit()
    if row.attempts > 6:
        return None  # too many tries — force a resend
    if not secrets.compare_digest(row.code_hash, _hash_code(code.strip())):
        return None
    return _mark_verified(db, row)


def verify_link(db: Session, token: str) -> User | None:
    """Verify by clicked link token. Returns the User on success, else None."""
    row = (
        db.query(EmailVerification)
        .filter(EmailVerification.link_token == token)
        .first()
    )
    if row is None or _expired(row):
        return None
    return _mark_verified(db, row)


def _expired(row: EmailVerification) -> bool:
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > exp


def _mark_verified(db: Session, row: EmailVerification) -> User | None:
    user = db.get(User, row.user_id)
    if user is None:
        return None
    user.email_verified = True
    db.query(EmailVerification).filter(
        EmailVerification.user_id == user.id
    ).delete(synchronize_session=False)
    db.commit()
    return user


def _send_email(to_email: str, name: str, code: str, link: str, ttl: int) -> None:
    """Send the verification email (code + link) via the SMTP sender."""
    from app.agents.k3_broadcast import _send_smtp_email
    subject = "Verify your Yexis Control Room email"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#111">
      <h2 style="color:#1a56db">Verify your email</h2>
      <p>Hi {name},</p>
      <p>Use this code to verify your Yexis Control Room account:</p>
      <p style="font-size:30px;font-weight:bold;letter-spacing:6px;margin:18px 0">{code}</p>
      <p>Or simply click the button below:</p>
      <p><a href="{link}" style="display:inline-block;background:#1a56db;color:#fff;
        padding:11px 20px;border-radius:6px;text-decoration:none">Verify my email</a></p>
      <p style="color:#666;font-size:13px">This code and link expire in {ttl} minutes.
      If you didn't request this, you can ignore this email.</p>
    </div>
    """
    # pace=False: single message, skip the bulk send delay.
    _send_smtp_email(to_email, subject, html, pace=False)
