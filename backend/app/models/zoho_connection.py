import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class ZohoUserConnection(Base):
    __tablename__ = "zoho_user_connection"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    refresh_token_encrypted: Mapped[str] = mapped_column(String(2048), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(100), nullable=False)
    organization_name: Mapped[str | None] = mapped_column(String(300))
    data_center: Mapped[str] = mapped_column(String(20), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ZohoOAuthState(Base):
    __tablename__ = "zoho_oauth_state"

    nonce_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
