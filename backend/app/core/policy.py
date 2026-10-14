import logging
from dataclasses import dataclass
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.task import Task
from app.models.user import User
from app.repository.approval_repository import ApprovalRepository
from app.repository.user_repository import UserRepository
from app.repository.policy_repository import (
    RiskClassificationRepository,
    PolicyRuleRepository,
    UserAgentPermissionRepository,
)


logger = logging.getLogger(__name__)


@dataclass
class PolicyDecision:
    """Result of the Policy Gate evaluation.

    Decision values:
        DENY             → task FAILED (blocked by policy)
        REQUIRE_APPROVAL → task WAITING_APPROVAL (needs human approval)
        ALLOW            → task EXECUTE (proceed to agent)
    """
    decision: str  # "DENY" | "REQUIRE_APPROVAL" | "ALLOW"
    reason: str = ""
    approval_type: str | None = None


class PolicyGate:
    """Data-driven Policy Gate.

    No hardcoded actions, risk levels, or autonomy logic.
    Everything is resolved from database tables:

        1. Who?              → Is the user valid and enabled?
        2. Which agent?      → Is the agent valid and enabled?
        3. Which action?     → risk_classifications table
        4. Permission?       → user_agent_permissions table
        5. Autonomy level?   → agent.autonomy_level (from agents table)
        6. Approval needed?  → policy_rules table

    Input:  user + task + agent + requested action + autonomy level
    Output: ALLOW | DENY | REQUIRE_APPROVAL
    """

    def __init__(self, db: Session):
        self.db = db

    def evaluate(
        self,
        agent: Agent,
        action: str,
        task: Task | None = None,
        requested_by: str | None = None,
    ) -> PolicyDecision:
        """Run the 6-step Policy Gate.

        Returns a PolicyDecision with decision = DENY | REQUIRE_APPROVAL | ALLOW.
        """

        # ── Step 1: Who? ──────────────────────────────────────────────
        # Distinguish "no identity supplied" from "identity rejected".
        # These have different fixes and used to share one message.
        if not requested_by:
            return PolicyDecision(
                decision="DENY",
                reason=(
                    "Task has no requested_by — the Policy Gate "
                    "cannot evaluate a task with no identity"
                ),
            )

        user = self._check_user(requested_by)
        if user is None:
            return PolicyDecision(
                decision="DENY",
                reason=(
                    f"User '{requested_by}' not found or disabled"
                ),
            )

        # ── Step 2: Which agent? ──────────────────────────────────────
        if not agent.enabled:
            return PolicyDecision(
                decision="DENY",
                reason=f"Agent {agent.agent_code} is disabled",
            )

        # ── Step 3: Which action? ─────────────────────────────────────
        risk_repo = RiskClassificationRepository(self.db)
        risk = risk_repo.classify_action(action)
        logger.info(
            "Policy Gate: action=%s risk=%s agent=%s user=%s",
            action, risk, agent.agent_code, requested_by,
        )

        # ── Step 4: Permission? ───────────────────────────────────────
        if not self._check_agent_permission(user, agent):
            return PolicyDecision(
                decision="DENY",
                reason=(
                    f"User '{requested_by}' not permitted "
                    f"to use agent '{agent.agent_code}'"
                ),
            )

        # ── Step 5: Autonomy level? ───────────────────────────────────
        # ── Step 6: Approval needed? ──────────────────────────────────
        return self._resolve_decision(
            autonomy_level=agent.autonomy_level,
            risk_level=risk,
            action=action,
        )

    def ensure_approval(
        self,
        decision: PolicyDecision,
        task: Task,
        agent: Agent,
        requested_by: str | None = None,
    ) -> bool:
        """If approval is needed, create one and check status.

        Returns True if the action can proceed (approval exists
        and is APPROVED). Returns False if a new approval request
        was created and the action should be blocked.
        """
        if decision.decision != "REQUIRE_APPROVAL":
            return True

        approval_repo = ApprovalRepository(self.db)

        # Check if already approved for this task
        approved = approval_repo.get_approved_by_task(task.id)

        if approved is not None:
            logger.info(
                "Approval %s already granted for task %s",
                approved.id,
                task.id,
            )
            return True

        # Check if approval is still pending
        pending = approval_repo.get_pending_by_task(task.id)

        if pending is not None:
            logger.info(
                "Approval %s pending for task %s",
                pending.id,
                task.id,
            )
            return False

        # No existing approval — create a new one
        approval = approval_repo.create(
            approval_type=decision.approval_type,
            task_id=task.id,
            requested_by=requested_by,
            payload={
                "task_type": task.task_type,
                "agent_code": agent.agent_code,
                "action": decision.approval_type,
                "reason": decision.reason,
            },
        )

        logger.info(
            "Created approval %s for task %s: %s",
            approval.id,
            task.id,
            decision.approval_type,
        )

        return False

    # ── Private helpers ───────────────────────────────────────────────

    def _check_user(self, requested_by: str | None) -> User | None:
        """Step 1: Validate the requesting user exists and is enabled."""
        if not requested_by:
            return None

        user_repo = UserRepository(self.db)
        return user_repo.get_by_username(requested_by)

    def _check_agent_permission(
        self, user: User, agent: Agent
    ) -> bool:
        """Step 4: Check if user has permission to use this agent.

        Queried from user_agent_permissions table.
        If no permission rows exist at all, allow everyone (opt-in model).
        Permission enforcement activates only after rows are added.
        """
        perm_repo = UserAgentPermissionRepository(self.db)

        # If no permissions defined anywhere, allow all (opt-in)
        if perm_repo.is_empty():
            return True

        return perm_repo.has_permission(user.id, agent.id)

    def _resolve_decision(
        self,
        autonomy_level: str,
        risk_level: str,
        action: str,
    ) -> PolicyDecision:
        """Steps 5-6: Look up decision from policy_rules table.

        Query: policy_rules WHERE autonomy_level = ? AND risk_level = ?
        Returns the decision (ALLOW, DENY, REQUIRE_APPROVAL).
        """
        rule_repo = PolicyRuleRepository(self.db)
        decision = rule_repo.get_decision(autonomy_level, risk_level)

        if decision is None:
            logger.warning(
                "No policy rule for autonomy=%s risk=%s — defaulting to DENY",
                autonomy_level, risk_level,
            )
            return PolicyDecision(
                decision="DENY",
                reason=(
                    f"No policy rule defined for "
                    f"autonomy_level='{autonomy_level}' "
                    f"risk_level='{risk_level}'"
                ),
            )

        if decision == "REQUIRE_APPROVAL":
            return PolicyDecision(
                decision="REQUIRE_APPROVAL",
                approval_type=f"{risk_level}:{action}",
                reason=(
                    f"autonomy={autonomy_level} risk={risk_level} "
                    f"→ {decision}"
                ),
            )

        return PolicyDecision(
            decision=decision,
            reason=(
                f"autonomy={autonomy_level} risk={risk_level} "
                f"→ {decision}"
            ),
        )
