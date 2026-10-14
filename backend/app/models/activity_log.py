"""Activity log — immutable audit trail of every user action in the portal."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Nullable so system/scheduler actions can also be logged
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    username: Mapped[str] = mapped_column(String(150), nullable=False, default="system")
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="system")

    # What happened: login, logout, task_create, demo_run, approval_approve,
    # approval_reject, tally_stage, tally_post, campaign_send, template_create,
    # user_create, user_update, lane_update, etc.
    action: Mapped[str] = mapped_column(String(100), nullable=False)

    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
