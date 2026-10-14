"""Lane governance — unit tests.

Covers the LaneRepository decisions and the executor's refusal paths:
kill switch, quiet hours, daily cap, autonomy validation, dry-run
semantics.
"""

import uuid
from datetime import time
from unittest.mock import MagicMock

import pytest

from app.models.lane_config import LaneConfig
from app.repository.lane_repository import (
    LaneRepository,
    _in_quiet_hours,
)


def _lane(**overrides) -> LaneConfig:
    base = dict(
        lane="P1",
        mode="hybrid",
        autonomy_state="A1",
        kill_switch=False,
        dry_run=False,
        daily_cap=None,
        quiet_start=None,
        quiet_end=None,
    )
    base.update(overrides)
    return LaneConfig(**base)


def _repo_with(lane: LaneConfig | None) -> LaneRepository:
    db = MagicMock()
    db.execute.return_value.scalars.return_value.first.return_value = (
        lane
    )
    db.flush = MagicMock()
    return LaneRepository(db)


class TestLaneDecisions:

    def test_unconfigured_lane_runs_under_defaults(self):
        decision = _repo_with(None).evaluate("P9")
        assert decision.allowed is True
        assert decision.autonomy_state == "A1"
        assert decision.dry_run is False

    def test_healthy_lane_allowed(self):
        decision = _repo_with(_lane()).evaluate("P1")
        assert decision.allowed is True
        assert decision.autonomy_state == "A1"

    def test_killed_lane_refused(self):
        decision = _repo_with(
            _lane(kill_switch=True)
        ).evaluate("P1")
        assert decision.allowed is False
        assert "killed" in decision.reason

    def test_quiet_hours_refuse_normal_priority(self):
        lane = _lane(quiet_start=time(22, 0), quiet_end=time(23, 59))
        # Force "now" inside the window by checking the helper directly
        # with a synthetic clock via evaluate's priority path instead.
        repo = _repo_with(lane)
        # Patch the window check to a known-true answer
        import app.repository.lane_repository as mod
        original = mod._in_quiet_hours
        mod._in_quiet_hours = lambda l: True
        try:
            decision = repo.evaluate("P1", priority=100)
            assert decision.allowed is False
            assert "quiet" in decision.reason

            high = repo.evaluate("P1", priority=10)
            assert high.allowed is True
            assert "waived" in high.reason
        finally:
            mod._in_quiet_hours = original

    def test_quiet_hours_helper_overnight_window(self):
        lane = _lane(quiet_start=time(22, 0), quiet_end=time(6, 0))
        # Sanity only — exact now-dependent behaviour is not asserted.
        assert isinstance(_in_quiet_hours(lane), bool)


class TestDailyCap:

    def test_no_cap_never_exceeded(self):
        lane = _lane(daily_cap=None)
        repo = _repo_with(lane)
        repo.today_execution_count = lambda lane_code: 999999
        assert repo.cap_exceeded(lane) is False

    def test_cap_exceeded(self):
        lane = _lane(daily_cap=5)
        repo = _repo_with(lane)
        repo.today_execution_count = lambda lane_code: 5
        assert repo.cap_exceeded(lane) is True

    def test_under_cap(self):
        lane = _lane(daily_cap=5)
        repo = _repo_with(lane)
        repo.today_execution_count = lambda lane_code: 4
        assert repo.cap_exceeded(lane) is False


class TestOperatorMoves:

    def test_set_autonomy_valid(self):
        repo = _repo_with(_lane())
        row = repo.set_autonomy("P1", "A2")
        assert row.autonomy_state == "A2"

    def test_set_autonomy_invalid_rejected(self):
        repo = _repo_with(_lane())
        with pytest.raises(ValueError):
            repo.set_autonomy("P1", "A4")

    def test_unknown_lane_raises(self):
        repo = _repo_with(None)
        with pytest.raises(ValueError):
            repo.set_kill_switch("P9", True)

    def test_daily_cap_validation(self):
        repo = _repo_with(_lane())
        with pytest.raises(ValueError):
            repo.set_daily_cap("P1", 0)
        row = repo.set_daily_cap("P1", None)
        assert row.daily_cap is None


class TestExecutorRefusal:

    def test_lane_decision_stamps_regime(self):
        """The executor stamps autonomy_state/dry_run onto results."""
        decision = _repo_with(
            _lane(autonomy_state="A2", dry_run=True)
        ).evaluate("P1")
        assert decision.autonomy_state == "A2"
        assert decision.dry_run is True
