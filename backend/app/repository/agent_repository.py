from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.task import Task
from app.models.action_ledger import ActionLedger


class AgentRepository:

    def __init__(self, db: Session):
        self.db = db

    def list_enabled(self) -> list[Agent]:
        statement = (
            select(Agent)
            .where(Agent.enabled.is_(True))
            .order_by(Agent.agent_code)
        )

        return list(self.db.scalars(statement).all())

    def list_all(self) -> list[Agent]:
        statement = (
            select(Agent)
            .order_by(Agent.agent_code)
        )

        return list(self.db.scalars(statement).all())

    def get_by_code(self, agent_code: str) -> Agent | None:
        statement = select(Agent).where(
            Agent.agent_code == agent_code
        )

        return self.db.scalar(statement)

    def get_task_stats(
        self,
        agent_code: str,
    ) -> dict:
        """Get task statistics for an agent from action_ledger."""
        # Count by status from action_ledger
        statement = (
            select(
                ActionLedger.status,
                func.count(ActionLedger.id),
            )
            .join(Agent, ActionLedger.agent_id == Agent.id)
            .where(Agent.agent_code == agent_code)
            .group_by(ActionLedger.status)
        )

        rows = self.db.execute(statement).all()
        counts = {row[0]: row[1] for row in rows}

        # Last execution time
        last_stmt = (
            select(func.max(ActionLedger.created_at))
            .join(Agent, ActionLedger.agent_id == Agent.id)
            .where(Agent.agent_code == agent_code)
        )
        last_executed = self.db.scalar(last_stmt)

        # Tasks currently running for this agent
        running_stmt = (
            select(func.count(Task.id))
            .where(
                Task.status == "RUNNING",
            )
        )
        # Note: task doesn't have agent_id directly;
        # we approximate from action_ledger recent runs

        return {
            "completed": counts.get("SUCCESS", 0),
            "failed": counts.get("FAILED", 0),
            "total_runs": sum(counts.values()),
            "last_executed_at": last_executed,
        }