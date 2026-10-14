import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, ForeignKey, Table, Column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.agent import Base


# Join table for many-to-many user <-> permission
user_permissions = Table(
    "user_permissions",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        UUID(as_uuid=True),
        ForeignKey("permissions.id"),
        primary_key=True,
    ),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    username: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        nullable=False,
    )

    display_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    email: Mapped[str | None] = mapped_column(
        String(254),
        unique=True,
        nullable=True,
    )

    # True once the user has proven ownership of `email` via a one-time code.
    # Login is blocked until this is True.
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Bcrypt hash — never store plaintext
    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Official office phone number (calls, SMS)
    phone_number: Mapped[str | None] = mapped_column(
        String(20),
        unique=True,
        nullable=True,
    )

    # WhatsApp number — may match phone_number or be different (personal WA)
    whatsapp_number: Mapped[str | None] = mapped_column(
        String(20),
        unique=True,
        nullable=True,
    )

    # Role: admin | finance | gsm | purchase | ops | viewer | system
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="viewer",
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    permissions = relationship(
        "Permission",
        secondary=user_permissions,
        back_populates="users",
    )


class EmailVerification(Base):
    """A pending email-ownership check. One active row per user.

    Holds BOTH a hashed 6-digit code (typed on the verify page) and a
    URL-safe link token (clicked from the email) — either one, before
    `expires_at`, proves ownership. Codes are stored hashed, never plaintext.
    """
    __tablename__ = "email_verification"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)

    # sha256 hex of the 6-digit code
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # URL-safe token embedded in the verification link
    link_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    permission_code: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    users = relationship(
        "User",
        secondary=user_permissions,
        back_populates="permissions",
    )
