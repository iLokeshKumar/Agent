"""Lane configuration — one row per agent lane, governing how it runs.

A "lane" is one agent's execution path through the platform. The docs'
governance model gives every lane three switches an operator can move
without a deploy:

    autonomy_state   A1 propose-only · A2 supervised · A3 bounded
    kill_switch      pulled = tasks in this lane are refused outright
    dry_run          agent executes and reports, but nothing is committed

Plus the hard guards from the Automation Gateway spec: a daily action
cap and quiet hours. Enforcement lives in the executor — it refuses a
killed lane, counts today's ledger entries against the cap, and stamps
every run with the lane's autonomy state so the audit trail records
not just what happened but under which governance regime.
"""

import uuid
from datetime import datetime, time

from sqlalchemy import DateTime, Integer, String, Text, Boolean, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base


class LaneConfig(Base):
    __tablename__ = "lane_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # The agent this lane governs, e.g. "P1". Unique — one config per
    # lane; agents not yet seeded run under executor defaults (A1,
    # no cap) rather than being blocked.
    lane: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
    )

    # supervised | headless | hybrid
    mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="supervised",
    )

    # A1 | A2 | A3 — earned through promotion gates, never set directly
    # to A3 in normal operation.
    autonomy_state: Mapped[str] = mapped_column(
        String(4),
        nullable=False,
        server_default="A1",
    )

    # Pulled: executor refuses every task in this lane immediately.
    kill_switch: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )

    # Agent runs and reports what it would do; commit is suppressed.
    dry_run: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )

    # Max successful executions per calendar day. NULL = uncapped.
    daily_cap: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Outside this window only high-priority work runs (priority < 50).
    quiet_start: Mapped[time | None] = mapped_column(
        Time(timezone=True),
        nullable=True,
    )

    quiet_end: Mapped[time | None] = mapped_column(
        Time(timezone=True),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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
        onupdate=datetime.utcnow,
    )
