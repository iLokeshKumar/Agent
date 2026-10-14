import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class RiskClassification(Base):
    """Maps action patterns to risk levels.

    Pattern matching:
        - Exact:  "DELETE" matches action "DELETE"
        - Prefix: "WRITE:" matches action "WRITE:CREATE_PO"
        - Wildcard: "APPROVE_*" matches "APPROVE_PAYMENT"

    Risk levels: ROUTINE | WRITE | HIGH
    """
    __tablename__ = "risk_classifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    action_pattern: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
    )

    risk_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )


class PolicyRule(Base):
    """Deterministic decision table.

    Each row: (autonomy_level, risk_level) → decision

    autonomy_level: A1 | A2 | A3
    risk_level:     ROUTINE | WRITE | HIGH
    decision:       ALLOW | DENY | REQUIRE_APPROVAL
    """
    __tablename__ = "policy_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    autonomy_level: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    risk_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    decision: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "autonomy_level",
            "risk_level",
            name="uq_policy_rule_autonomy_risk",
        ),
    )


class UserAgentPermission(Base):
    """Controls which users can trigger which agents.

    If no row exists for a user+agent pair, access is DENIED.
    If a row exists with enabled=True, access is ALLOWED.
    """
    __tablename__ = "user_agent_permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "agent_id",
            name="uq_user_agent_permission",
        ),
    )
