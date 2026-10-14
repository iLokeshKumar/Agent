from enum import Enum
from pydantic import BaseModel


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class PolicyResult(BaseModel):
    decision: PolicyDecision
    reason: str
    agent_code: str
    action: str
    approval_type: str | None = None
    permission_code: str | None = None