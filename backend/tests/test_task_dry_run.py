from unittest.mock import MagicMock

from app.api.task import _is_dry_run_lane
from app.agents.base import AgentResult
from app.models.lane_config import LaneConfig
from orchestrator.executor import _run_in_dry_run


class _ToolCallingAgent:
    def __init__(self, error=None):
        self.error = error
        self.tool_called = False

    def validate(self, payload):
        return self.error

    def run(self, **kwargs):
        self.tool_called = True
        return AgentResult(status="SUCCESS")


def test_purchase_intake_dry_run_suppresses_document_persistence():
    db = MagicMock()
    db.execute.return_value.scalars.return_value.first.return_value = (
        LaneConfig(lane="P1", mode="hybrid", dry_run=True)
    )

    assert _is_dry_run_lane(db, "P1") is True


def test_purchase_intake_without_dry_run_allows_normal_persistence():
    db = MagicMock()
    db.execute.return_value.scalars.return_value.first.return_value = (
        LaneConfig(lane="P1", mode="hybrid", dry_run=False)
    )

    assert _is_dry_run_lane(db, "P1") is False


def test_dry_run_validates_without_calling_agent_tools():
    agent = _ToolCallingAgent()

    result = _run_in_dry_run(agent, {"safe": True})

    assert result.status == "SUCCESS"
    assert agent.tool_called is False


def test_dry_run_returns_input_validation_errors():
    result = _run_in_dry_run(_ToolCallingAgent("missing item"), {})

    assert result.status == "VALIDATION_ERROR"
    assert result.error == "missing item"
