"""Lane governance — read and move the three switches.

The executor asks one question here: *may this task run, and under
what regime?* Everything else is operator tooling: flip autonomy,
pull or release the kill switch, toggle dry-run.
"""

import logging
import uuid
from datetime import datetime, timezone, date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.action_ledger import ActionLedger
from app.models.lane_config import LaneConfig


logger = logging.getLogger(__name__)

VALID_AUTONOMY = ("A1", "A2", "A3")


class LaneDecision:
    """Executor's verdict for one task, with reasons it can log."""

    def __init__(
        self,
        allowed: bool,
        lane: LaneConfig | None,
        reason: str = "",
    ):
        self.allowed = allowed
        self.lane = lane
        self.reason = reason

    @property
    def autonomy_state(self) -> str:
        return self.lane.autonomy_state if self.lane else "A1"

    @property
    def dry_run(self) -> bool:
        return self.lane.dry_run if self.lane else False


class LaneRepository:

    def __init__(self, db: Session):
        self.db = db

    # ── Reads ────────────────────────────────────────────────────────

    def get(self, lane: str) -> LaneConfig | None:
        return self.db.execute(
            select(LaneConfig).where(LaneConfig.lane == lane)
        ).scalars().first()

    def list(self) -> list[LaneConfig]:
        return list(
            self.db.execute(
                select(LaneConfig).order_by(LaneConfig.lane.asc())
            ).scalars()
        )

    def evaluate(self, agent_code: str, priority: int = 100) -> LaneDecision:
        """Decide whether a task in this lane may run right now.

        Order: kill-switch (absolute) → quiet hours (waived for priority<50)
        → daily cap (never waived). An unconfigured lane runs under
        executor defaults — new agents must not be blocked by missing config.
        """
        lane = self.get(agent_code)

        if lane is None:
            return LaneDecision(allowed=True, lane=None,
                                reason="no lane config — defaults apply")

        if lane.kill_switch:
            return LaneDecision(allowed=False, lane=lane,
                                reason=f"lane {lane.lane} killed")

        if _in_quiet_hours(lane):
            if priority >= 50:
                return LaneDecision(allowed=False, lane=lane,
                                    reason="quiet hours")
            # High-priority work bypasses quiet hours.
            quiet_reason = "high priority — quiet hours waived"
        else:
            quiet_reason = ""

        if self.cap_exceeded(lane):
            return LaneDecision(allowed=False, lane=lane,
                                reason=f"daily cap {lane.daily_cap} reached")

        return LaneDecision(allowed=True, lane=lane, reason=quiet_reason)

    def today_execution_count(self, lane: str) -> int:
        """Successful executions recorded in the ledger since midnight UTC."""
        start = datetime.combine(date.today(), datetime.min.time(),
                                 tzinfo=timezone.utc)
        return int(
            self.db.execute(
                select(func.count()).select_from(ActionLedger).where(
                    ActionLedger.tool_name == lane,
                    ActionLedger.status == "SUCCESS",
                    ActionLedger.created_at >= start,
                )
            ).scalar() or 0
        )

    def cap_exceeded(self, lane: LaneConfig) -> bool:
        if lane.daily_cap is None:
            return False
        return self.today_execution_count(lane.lane) >= lane.daily_cap

    # ── Operator moves ───────────────────────────────────────────────

    def ensure(self, lane: str, **defaults) -> LaneConfig:
        """Get-or-create with defaults. Idempotent; never overwrites."""
        existing = self.get(lane)
        if existing is not None:
            return existing

        row = LaneConfig(id=uuid.uuid4(), lane=lane, **defaults)
        self.db.add(row)
        self.db.flush()
        logger.info("Seeded lane config for %s", lane)
        return row

    def set_autonomy(self, lane: str, state: str) -> LaneConfig:
        if state not in VALID_AUTONOMY:
            raise ValueError(
                f"autonomy must be one of {VALID_AUTONOMY}, got {state}"
            )
        row = self._must_get(lane)
        row.autonomy_state = state
        self.db.flush()
        logger.info("Lane %s autonomy → %s", lane, state)
        return row

    def set_kill_switch(self, lane: str, pulled: bool) -> LaneConfig:
        row = self._must_get(lane)
        row.kill_switch = pulled
        self.db.flush()
        logger.warning("Lane %s kill switch %s",
                       lane, "PULLED" if pulled else "released")
        return row

    def set_dry_run(self, lane: str, enabled: bool) -> LaneConfig:
        row = self._must_get(lane)
        row.dry_run = enabled
        self.db.flush()
        logger.info("Lane %s dry run → %s", lane, enabled)
        return row

    def set_daily_cap(self, lane: str, cap: int | None) -> LaneConfig:
        if cap is not None and cap < 1:
            raise ValueError("daily_cap must be >= 1 or null")
        row = self._must_get(lane)
        row.daily_cap = cap
        self.db.flush()
        return row

    def _must_get(self, lane: str) -> LaneConfig:
        row = self.get(lane)
        if row is None:
            raise ValueError(f"unknown lane: {lane}")
        return row


def _in_quiet_hours(lane: LaneConfig) -> bool:
    if lane.quiet_start is None or lane.quiet_end is None:
        return False
    now = datetime.now(timezone.utc).timetz().replace(tzinfo=None)
    start = lane.quiet_start.replace(tzinfo=None)
    end = lane.quiet_end.replace(tzinfo=None)
    if start <= end:
        return start <= now < end
    # Overnight window (e.g. 22:00 → 06:00)
    return now >= start or now < end
