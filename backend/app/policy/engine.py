from .models import PolicyDecision, PolicyResult
from .repository import PolicyRepository


TASK_POLICY = {
    "collections": {
        "permission": "collections.execute",
        "action": "collections.execute",
    },
    "purchase_request": {
        "permission": "purchase.create",
        "action": "purchase.create",
    },
    "invoice_matching": {
        "permission": "invoice.match",
        "action": "invoice.match",
    },
    "service_request": {
        "permission": "service.execute",
        "action": "service.execute",
    },
    "grn_request": {
        "permission": "grn.create",
        "action": "grn.create",
    },
    "tally_post": {
        "permission": "tally.post",
        "action": "tally.post",
    },
    "dashboard_query": {
        "permission": "dashboard.read",
        "action": "dashboard.read",
    },
}


class PolicyEngine:

    def __init__(self, repository: PolicyRepository):
        self.repository = repository

    def evaluate(
        self,
        *,
        username: str,
        agent_code: str,
        autonomy_level: str,
        task_type: str,
    ) -> PolicyResult:

        policy = TASK_POLICY.get(task_type)

        if not policy:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"No policy mapping for task type: {task_type}",
                agent_code=agent_code,
                action=task_type,
            )

        permission_code = policy["permission"]
        action = policy["action"]

        has_permission = self.repository.user_has_permission(
            username=username,
            permission_code=permission_code,
        )

        if not has_permission:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Missing permission: {permission_code}",
                agent_code=agent_code,
                action=action,
                permission_code=permission_code,
            )

        if autonomy_level == "A1":
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="Permission granted; A1 agent may execute autonomously",
                agent_code=agent_code,
                action=action,
                permission_code=permission_code,
            )

        if autonomy_level == "A2":
            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason="Permission granted; A2 agent requires human approval",
                agent_code=agent_code,
                action=action,
                approval_type="STANDARD",
                permission_code=permission_code,
            )

        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason=f"Unsupported autonomy level: {autonomy_level}",
            agent_code=agent_code,
            action=action,
            permission_code=permission_code,
        )