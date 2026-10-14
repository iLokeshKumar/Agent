"""Employee / user management API.

GET    /users                  — list all users
POST   /users                  — create employee
PATCH  /users/{id}             — update fields
DELETE /users/{id}             — disable (soft delete)
GET    /users/by-wa/{number}   — look up by WhatsApp number (E.164)
POST   /users/{id}/set-password — set or change password
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.config import settings
from app.core.validation import validate_email, validate_phone, validate_whatsapp
from app.models.user import User


def _allowed_domains() -> list[str] | None:
    raw = (settings.allowed_email_domains or "").strip()
    if not raw:
        return None
    return [d.strip() for d in raw.split(",") if d.strip()]

router = APIRouter(prefix="/users", tags=["users"])


def _require_admin(request: Request):
    from app.core.auth import get_current_user

    actor = get_current_user(request)
    if not actor or actor.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return actor


def _hash_password(plain: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # fallback: sha256 — install bcrypt for production
        import hashlib
        return "sha256:" + hashlib.sha256(plain.encode()).hexdigest()


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        import bcrypt
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ImportError:
        import hashlib
        return hashed == "sha256:" + hashlib.sha256(plain.encode()).hexdigest()


class UserCreate(BaseModel):
    username: str | None = None           # optional — defaults to the email
    display_name: str | None = None
    email: str | None = None
    role: str | None = None               # admin|finance|gsm|purchase|ops|viewer
    password: str | None = None
    phone_number: str | None = None       # office phone, E.164
    whatsapp_number: str | None = None    # may differ from phone_number


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    phone_number: str | None = None
    whatsapp_number: str | None = None
    enabled: bool | None = None


class SetPassword(BaseModel):
    password: str


def _fmt(u: User) -> dict:
    return {
        "id": str(u.id),
        "username": u.username,
        "display_name": u.display_name,
        "email": u.email,
        "role": u.role,
        "email_verified": bool(getattr(u, "email_verified", False)),
        "phone_number": u.phone_number,
        "whatsapp_number": u.whatsapp_number,
        "enabled": u.enabled,
        "has_password": bool(u.password_hash),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _check_unique(db: Session, field: str, value: str, exclude_id=None):
    q = db.query(User).filter(getattr(User, field) == value)
    if exclude_id:
        q = q.filter(User.id != exclude_id)
    if q.first():
        raise HTTPException(status_code=409, detail=f"{field} already in use")


@router.get("")
def list_users(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    return [_fmt(u) for u in db.query(User).order_by(User.display_name).all()]


@router.post("", status_code=201)
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    from app.models.access_control import RoleDefinition

    email = None
    if body.email:
        try:
            email = validate_email(body.email, allowed_domains=_allowed_domains())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _check_unique(db, "email", email)

    # Username is optional — default to the email (or its local-part) so admins
    # can create accounts with just an email.
    username = (body.username or "").strip()
    if not username:
        if not email:
            raise HTTPException(status_code=422, detail="Provide a username or an email.")
        username = email
    _check_unique(db, "username", username)

    role = (body.role or "viewer").strip()
    if db.get(RoleDefinition, role) is None:
        raise HTTPException(status_code=422, detail="Role not found.")
    if role == "system":
        raise HTTPException(
            status_code=422,
            detail="The system role is reserved for the gateway service account.",
        )

    phone = None
    if body.phone_number:
        try:
            phone = validate_phone(body.phone_number, label="phone_number")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _check_unique(db, "phone_number", phone)

    wa = None
    if body.whatsapp_number:
        try:
            wa = validate_whatsapp(body.whatsapp_number)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _check_unique(db, "whatsapp_number", wa)

    user = User(
        id=uuid.uuid4(),
        username=username,
        display_name=body.display_name,
        email=email,
        role=role,
        email_verified=False,
        password_hash=_hash_password(body.password) if body.password else None,
        phone_number=phone,
        whatsapp_number=wa,
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()

    # Kick off email-ownership verification (6-digit code + link). Non-fatal:
    # if SMTP fails the account still exists and a code can be resent later.
    verification_sent = False
    if email and settings.require_email_verification:
        try:
            from app.services import email_otp
            email_otp.generate_and_send(db, user)
            verification_sent = True
        except Exception:
            verification_sent = False

    out = _fmt(user)
    out["role"] = user.role
    out["email_verified"] = user.email_verified
    out["verification_email_sent"] = verification_sent
    return out


@router.patch("/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    if body.display_name is not None:
        user.display_name = body.display_name
    if body.email is not None:
        try:
            body.email = validate_email(body.email, allowed_domains=_allowed_domains())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _check_unique(db, "email", body.email, exclude_id=user.id)
        user.email = body.email
    if body.phone_number is not None:
        try:
            body.phone_number = validate_phone(body.phone_number, label="phone_number")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _check_unique(db, "phone_number", body.phone_number, exclude_id=user.id)
        user.phone_number = body.phone_number
    if body.whatsapp_number is not None:
        try:
            body.whatsapp_number = validate_whatsapp(body.whatsapp_number)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _check_unique(db, "whatsapp_number", body.whatsapp_number, exclude_id=user.id)
        user.whatsapp_number = body.whatsapp_number
    if body.enabled is not None:
        user.enabled = body.enabled

    db.commit()
    return _fmt(user)


@router.post("/{user_id}/set-password", status_code=200)
def set_password(
    user_id: str,
    body: SetPassword,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    user.password_hash = _hash_password(body.password)
    db.commit()
    return {"status": "password updated"}


@router.delete("/{user_id}")
def disable_user(user_id: str, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    user.enabled = False
    db.commit()
    return {"status": "disabled", "id": user_id}


@router.get("/by-wa/{number:path}")
def get_user_by_whatsapp(number: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.whatsapp_number == number).first()
    if not user:
        raise HTTPException(status_code=404, detail="no user registered for this number")
    return _fmt(user)
