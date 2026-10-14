"""Local username+password authentication for the control-room portal.

POST /auth/login   — username + password (form or JSON) → sets httpOnly cookie
POST /auth/logout  — clears the session cookie
GET  /auth/me      — current user info (requires valid session)
PATCH /auth/users/{id}/role  — set a user's role (admin only)
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import (
    COOKIE_NAME, create_token, get_current_user, log_activity
)
from app.core.db import get_db
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth-local"])


def _hash(plain: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        import hashlib
        return "sha256:" + hashlib.sha256(plain.encode()).hexdigest()


def _verify(plain: str, hashed: str) -> bool:
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


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #

class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    """Accept JSON body OR HTML form submission.

    The identity field may be a username OR an email address (login by email
    keeps the email private-optional — users can sign in either way).
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        identifier = body.get("username") or body.get("email") or body.get("identifier") or ""
        password = body.get("password", "")
    else:
        form = await request.form()
        identifier = form.get("username") or form.get("email") or form.get("identifier") or ""
        password = form.get("password", "")

    identifier = (identifier or "").strip()
    # An "@" means they typed an email; otherwise treat it as a username.
    if "@" in identifier:
        user = db.query(User).filter(User.email == identifier.lower()).first()
    else:
        user = db.query(User).filter(User.username == identifier).first()

    if not user or not user.enabled:
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    if not user.password_hash:
        raise HTTPException(
            status_code=401,
            detail="No password set for this account — ask an admin to set one",
        )

    if not _verify(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    # Block login until the email is verified (system/service accounts and
    # accounts with no email are exempt).
    from app.core.config import settings as _settings
    if (_settings.require_email_verification and user.email
            and not user.email_verified and user.role != "system"):
        raise HTTPException(
            status_code=403,
            detail="Email not verified. Check your inbox for the 6-digit code or "
                   "verification link, then verify at /verify.",
        )

    # Update last_login_at
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    token = create_token(str(user.id), user.username, user.role or "viewer")

    log_activity(
        db, None, "login",
        resource_type="user", resource_id=str(user.id),
        detail={"username": user.username, "role": user.role},
        ip=_ip(request),
    )

    resp: Response
    if "application/json" in content_type:
        resp = JSONResponse({
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "token": token,
        })
    else:
        resp = RedirectResponse(url="/", status_code=303)

    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=8 * 3600,
        path="/",
    )
    return resp


# --------------------------------------------------------------------------- #
# Logout
# --------------------------------------------------------------------------- #

@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user:
        log_activity(db, user, "logout", ip=_ip(request))
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# --------------------------------------------------------------------------- #
# Me
# --------------------------------------------------------------------------- #

@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    row = db.query(User).filter(User.username == user.username).first()
    return {
        "user_id": user.user_id,
        "username": user.username,
        "display_name": row.display_name if row else user.username,
        "role": user.role,
        "last_login_at": row.last_login_at.isoformat() if (row and row.last_login_at) else None,
    }


# --------------------------------------------------------------------------- #
# Role assignment (admin only)
# --------------------------------------------------------------------------- #

class RoleUpdate(BaseModel):
    role: str


class RoleCreate(BaseModel):
    name: str


class RoleGrantsUpdate(BaseModel):
    grants: list[tuple[str, str]] = Field(default_factory=list)


def _require_admin(request: Request):
    actor = get_current_user(request)
    if not actor or actor.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return actor


@router.get("/roles")
def list_role_definitions(
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request)
    from app.models.access_control import RoleDefinition
    from app.services.role_access import get_role_grants
    return [
        {
            "name": role.name,
            "built_in": role.built_in,
            "grants": sorted(get_role_grants(db, role.name)),
        }
        for role in db.query(RoleDefinition).order_by(RoleDefinition.name).all()
    ]


@router.post("/roles", status_code=201)
def create_role_definition(
    body: RoleCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = _require_admin(request)
    from app.services.role_access import create_role
    try:
        role = create_role(db, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log_activity(
        db, actor, "role_create", resource_type="role", resource_id=role.name,
        detail={"name": role.name}, ip=_ip(request),
    )
    return {"name": role.name, "built_in": role.built_in, "grants": []}


@router.put("/roles/{role}/permissions")
def update_role_permissions(
    role: str,
    body: RoleGrantsUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = _require_admin(request)
    from app.services.role_access import set_role_grants
    try:
        grants = set(body.grants)
        set_role_grants(db, role, grants)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log_activity(
        db, actor, "role_permissions_update", resource_type="role",
        resource_id=role, detail={"grant_count": len(grants)}, ip=_ip(request),
    )
    return {"name": role, "grants": sorted(grants)}


@router.delete("/roles/{role}")
def delete_role_definition(
    role: str,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = _require_admin(request)
    from app.services.role_access import delete_role
    try:
        delete_role(db, role)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_activity(
        db, actor, "role_delete", resource_type="role", resource_id=role,
        detail={"name": role}, ip=_ip(request),
    )
    return {"status": "deleted", "name": role}


@router.patch("/users/{user_id}/role")
def set_role(
    user_id: str,
    body: RoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = _require_admin(request)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    from app.models.access_control import RoleDefinition
    if db.get(RoleDefinition, body.role) is None:
        raise HTTPException(status_code=422, detail="Role not found.")
    if body.role == "system":
        raise HTTPException(status_code=422, detail="The system role is reserved for the gateway service account.")
    old_role = target.role
    target.role = body.role
    db.commit()
    log_activity(
        db, actor, "user_role_change",
        resource_type="user", resource_id=user_id,
        detail={"from": old_role, "to": body.role, "target_username": target.username},
        ip=_ip(request),
    )
    return {"id": user_id, "username": target.username, "role": target.role}


# --------------------------------------------------------------------------- #
# Set password (admin or self)
# --------------------------------------------------------------------------- #

class PasswordSet(BaseModel):
    password: str


@router.post("/users/{user_id}/set-password")
def set_password(
    user_id: str,
    body: PasswordSet,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = get_current_user(request)
    if not actor:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Admin may set any password; others may only set their own
    if actor.role != "admin" and actor.user_id != user_id:
        raise HTTPException(status_code=403, detail="Cannot set another user's password")
    if len(body.password) < 6:
        raise HTTPException(status_code=422, detail="Password must be at least 6 characters")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.password_hash = _hash(body.password)
    db.commit()
    log_activity(
        db, actor, "password_change",
        resource_type="user", resource_id=user_id,
        ip=_ip(request),
    )
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Email verification — 6-digit code and/or verification link
# --------------------------------------------------------------------------- #

class SendCodeBody(BaseModel):
    # username or email — whichever the user has
    identifier: str


@router.post("/send-code")
def send_verification_code(body: SendCodeBody, db: Session = Depends(get_db)):
    """(Re)send a verification code + link to a user's email.

    Returns a generic success even if the account isn't found, to avoid
    revealing which emails/usernames exist.
    """
    from app.services import email_otp

    ident = (body.identifier or "").strip()
    if "@" in ident:
        user = db.query(User).filter(User.email == ident.lower()).first()
    else:
        user = db.query(User).filter(User.username == ident).first()

    if user and user.email and not user.email_verified:
        try:
            email_otp.generate_and_send(db, user)
        except Exception as exc:  # SMTP down, etc. — don't leak details
            raise HTTPException(status_code=502, detail=f"Could not send verification email: {exc}")
    return {"status": "sent", "message": "If the account exists, a verification code and link were emailed."}


class VerifyCodeBody(BaseModel):
    email: str
    code: str
    password: str | None = None   # optional: set a password at the same time


@router.post("/verify-email")
def verify_email_code(body: VerifyCodeBody, request: Request, db: Session = Depends(get_db)):
    """Verify by typed 6-digit code. Optionally sets the password in the same
    step (used by the invite flow where the admin didn't set one)."""
    from app.services import email_otp

    user = email_otp.verify_code(db, body.email, body.code)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code. Request a new one.")

    if body.password:
        if len(body.password) < 6:
            raise HTTPException(status_code=422, detail="Password must be at least 6 characters")
        user.password_hash = _hash(body.password)
        db.commit()

    log_activity(
        db, None, "email_verified",
        resource_type="user", resource_id=str(user.id),
        detail={"email": user.email, "via": "code"}, ip=_ip(request),
    )
    return {"status": "verified", "username": user.username, "email": user.email,
            "has_password": bool(user.password_hash)}


@router.get("/verify-link")
def verify_email_link(token: str, request: Request, db: Session = Depends(get_db)):
    """Verify by clicking the link in the email, then land on the login page."""
    from app.services import email_otp

    user = email_otp.verify_link(db, token)
    if user is None:
        return RedirectResponse(url="/login?verify=invalid", status_code=303)
    log_activity(
        db, None, "email_verified",
        resource_type="user", resource_id=str(user.id),
        detail={"email": user.email, "via": "link"}, ip=_ip(request),
    )
    # If they still have no password, send them to set one; else to login.
    if not user.password_hash:
        return RedirectResponse(url=f"/verify?email={user.email}&verified=1", status_code=303)
    return RedirectResponse(url="/login?verify=ok", status_code=303)
