import uuid
import fnmatch
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.policy import RiskClassification, PolicyRule, UserAgentPermission


class RiskClassificationRepository:

    def __init__(self, db: Session):
        self.db = db

    def classify_action(self, action: str) -> str:
        """Match an action against risk classification patterns.

        Returns the risk_level (ROUTINE, WRITE, HIGH) or ROUTINE as default.
        """
        statement = (
            select(RiskClassification)
            .where(RiskClassification.enabled.is_(True))
            .order_by(RiskClassification.action_pattern)
        )

        classifications = list(
            self.db.execute(statement).scalars().all()
        )

        for cls in classifications:
            pattern = cls.action_pattern

            # Exact match
            if action == pattern:
                return cls.risk_level

            # Wildcard match (e.g. "APPROVE_*")
            if "*" in pattern:
                if fnmatch.fnmatch(action, pattern):
                    return cls.risk_level

            # Prefix match (e.g. "WRITE:" matches "WRITE:CREATE_PO")
            if pattern.endswith(":"):
                if action.startswith(pattern):
                    return cls.risk_level

        return "ROUTINE"

    def list_all(self) -> list[RiskClassification]:
        statement = (
            select(RiskClassification)
            .where(RiskClassification.enabled.is_(True))
            .order_by(RiskClassification.action_pattern)
        )
        return list(self.db.execute(statement).scalars().all())


class PolicyRuleRepository:

    def __init__(self, db: Session):
        self.db = db

    def get_decision(
        self,
        autonomy_level: str,
        risk_level: str,
    ) -> str | None:
        """Look up the decision for an autonomy_level + risk_level pair.

        Returns the decision string (ALLOW, DENY, REQUIRE_APPROVAL)
        or None if no rule exists.
        """
        statement = (
            select(PolicyRule)
            .where(
                PolicyRule.autonomy_level == autonomy_level,
                PolicyRule.risk_level == risk_level,
                PolicyRule.enabled.is_(True),
            )
        )

        rule = self.db.execute(statement).scalar_one_or_none()

        if rule is None:
            return None

        return rule.decision

    def list_all(self) -> list[PolicyRule]:
        statement = (
            select(PolicyRule)
            .where(PolicyRule.enabled.is_(True))
            .order_by(
                PolicyRule.autonomy_level,
                PolicyRule.risk_level,
            )
        )
        return list(self.db.execute(statement).scalars().all())


class UserAgentPermissionRepository:

    def __init__(self, db: Session):
        self.db = db

    def is_empty(self) -> bool:
        """Check if any permission rows exist.

        Returns True if the table has zero rows (opt-in model).
        """
        from sqlalchemy import func
        count = self.db.execute(
            select(func.count()).select_from(UserAgentPermission)
        ).scalar()
        return count == 0

    def has_permission(
        self,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> bool:
        """Check if a user has permission to use an agent.

        Returns True if an enabled permission row exists.
        Returns False if no row exists or row is disabled.
        """
        statement = (
            select(UserAgentPermission)
            .where(
                UserAgentPermission.user_id == user_id,
                UserAgentPermission.agent_id == agent_id,
                UserAgentPermission.enabled.is_(True),
            )
        )

        perm = self.db.execute(statement).scalar_one_or_none()
        return perm is not None

    def list_for_user(
        self,
        user_id: uuid.UUID,
    ) -> list[UserAgentPermission]:
        statement = (
            select(UserAgentPermission)
            .where(
                UserAgentPermission.user_id == user_id,
                UserAgentPermission.enabled.is_(True),
            )
        )
        return list(self.db.execute(statement).scalars().all())
