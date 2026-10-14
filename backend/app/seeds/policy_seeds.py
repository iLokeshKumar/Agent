"""Seed default risk classifications and policy rules.

Run once to populate the database with the default policy configuration.
These match the original hardcoded behavior:
    - ROUTINE actions: READ, LIST, SEARCH, QUERY, TASK_EXECUTION
    - WRITE actions: CREATE_PO, UPDATE_PO, etc.
    - HIGH actions: APPROVE_PAYMENT, DELETE, etc.
    - A1: ROUTINE→ALLOW, WRITE→REQUIRE_APPROVAL, HIGH→REQUIRE_APPROVAL
    - A2: ROUTINE→ALLOW, WRITE→REQUIRE_APPROVAL, HIGH→REQUIRE_APPROVAL
    - A3: ROUTINE→ALLOW, WRITE→ALLOW, HIGH→REQUIRE_APPROVAL
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.policy import RiskClassification, PolicyRule


RISK_CLASSIFICATIONS = [
    # ROUTINE
    {"action_pattern": "TASK_EXECUTION", "risk_level": "ROUTINE"},
    {"action_pattern": "READ",           "risk_level": "ROUTINE"},
    {"action_pattern": "LIST",           "risk_level": "ROUTINE"},
    {"action_pattern": "SEARCH",         "risk_level": "ROUTINE"},
    {"action_pattern": "QUERY",          "risk_level": "ROUTINE"},

    # WRITE
    {"action_pattern": "CREATE_PO",              "risk_level": "WRITE"},
    {"action_pattern": "UPDATE_PO",              "risk_level": "WRITE"},
    {"action_pattern": "UPDATE_INVOICE",         "risk_level": "WRITE"},
    {"action_pattern": "RECEIVE_GRN",            "risk_level": "WRITE"},
    {"action_pattern": "UPDATE_SERVICE_REQUEST", "risk_level": "WRITE"},
    {"action_pattern": "LOG_ENTRY",              "risk_level": "WRITE"},
    {"action_pattern": "GENERATED_PO_DRAFT",     "risk_level": "WRITE"},
    {"action_pattern": "CAPTURED_SERIAL_NUMBERS", "risk_level": "WRITE"},
    {"action_pattern": "GENERATED_REMINDERS",    "risk_level": "WRITE"},
    {"action_pattern": "GENERATED_TALLY_FORMAT", "risk_level": "WRITE"},

    # HIGH
    {"action_pattern": "APPROVE_PAYMENT",      "risk_level": "HIGH"},
    {"action_pattern": "APPROVE_PO",           "risk_level": "HIGH"},
    {"action_pattern": "DELETE",               "risk_level": "HIGH"},
    {"action_pattern": "MODIFY_FINANCIAL_RECORD", "risk_level": "HIGH"},
    {"action_pattern": "SERIAL_OVERRIDE",      "risk_level": "HIGH"},
    {"action_pattern": "DISCOUNT_OVERRIDE",    "risk_level": "HIGH"},
    {"action_pattern": "FLAGGED_FOR_LEGAL",    "risk_level": "HIGH"},
    {"action_pattern": "FLAGGED_OVER_BUDGET",  "risk_level": "HIGH"},

    # Phase 2 — Finance actions
    {"action_pattern": "SUBMIT_SCHEME_CLAIM",     "risk_level": "WRITE"},
    {"action_pattern": "RECON_INTERENTITY",       "risk_level": "ROUTINE"},
    {"action_pattern": "GENERATE_CASH_FORECAST",  "risk_level": "ROUTINE"},

    # Phase 2 — HR actions
    {"action_pattern": "OPEN_JOB_REQUISITION",    "risk_level": "WRITE"},
    {"action_pattern": "POST_JOB",                "risk_level": "HIGH"},  # external posting
    {"action_pattern": "START_ONBOARDING",        "risk_level": "WRITE"},
    {"action_pattern": "COMPLETE_ONBOARDING",     "risk_level": "WRITE"},
    {"action_pattern": "SCORE_ATTRITION_RISK",    "risk_level": "ROUTINE"},
    {"action_pattern": "VALIDATE_PAYROLL",        "risk_level": "HIGH"},  # pre-payment check

    # Phase 3 — Stores
    {"action_pattern": "OPEN_COUNT_BATCH",        "risk_level": "WRITE"},
    {"action_pattern": "APPROVE_PHYSICAL_COUNT",  "risk_level": "HIGH"},

    # Phase 3 — Service
    {"action_pattern": "RENEW_AMC",               "risk_level": "WRITE"},
    {"action_pattern": "REGISTER_AMC_CONTRACT",   "risk_level": "WRITE"},

    # Phase 3 — SCM
    {"action_pattern": "SUBMIT_INDENT",           "risk_level": "WRITE"},
    {"action_pattern": "RECORD_DISPATCH",         "risk_level": "WRITE"},
    {"action_pattern": "RECORD_POD",              "risk_level": "WRITE"},
    {"action_pattern": "RAISE_SCM_CLAIM",         "risk_level": "WRITE"},
    {"action_pattern": "SETTLE_CLAIM",            "risk_level": "HIGH"},

    # Phase 3 — Campaigns
    {"action_pattern": "CREATE_CAMPAIGN",         "risk_level": "WRITE"},
    {"action_pattern": "BUILD_AUDIENCE",          "risk_level": "WRITE"},
    {"action_pattern": "BROADCAST_WA",            "risk_level": "HIGH"},  # mass messaging
    {"action_pattern": "COMPUTE_INCENTIVE",       "risk_level": "ROUTINE"},
    {"action_pattern": "DISBURSE_INCENTIVE",      "risk_level": "HIGH"},
    {"action_pattern": "CLASSIFY_RESPONSE",       "risk_level": "WRITE"},
    {"action_pattern": "COMPUTE_CAMPAIGN_KPIS",   "risk_level": "ROUTINE"},
]

POLICY_RULES = [
    # A1: Read-only / advisory
    {"autonomy_level": "A1", "risk_level": "ROUTINE", "decision": "ALLOW"},
    {"autonomy_level": "A1", "risk_level": "WRITE",   "decision": "REQUIRE_APPROVAL"},
    {"autonomy_level": "A1", "risk_level": "HIGH",    "decision": "REQUIRE_APPROVAL"},

    # A2: Supervised automation
    {"autonomy_level": "A2", "risk_level": "ROUTINE", "decision": "ALLOW"},
    {"autonomy_level": "A2", "risk_level": "WRITE",   "decision": "REQUIRE_APPROVAL"},
    {"autonomy_level": "A2", "risk_level": "HIGH",    "decision": "REQUIRE_APPROVAL"},

    # A3: Full autonomy
    {"autonomy_level": "A3", "risk_level": "ROUTINE", "decision": "ALLOW"},
    {"autonomy_level": "A3", "risk_level": "WRITE",   "decision": "ALLOW"},
    {"autonomy_level": "A3", "risk_level": "HIGH",    "decision": "REQUIRE_APPROVAL"},
]


def seed_policy(db: Session) -> None:
    """Insert default risk classifications and policy rules."""
    now = datetime.now(timezone.utc)

    # Seed risk classifications
    for item in RISK_CLASSIFICATIONS:
        existing = (
            db.query(RiskClassification)
            .filter(
                RiskClassification.action_pattern == item["action_pattern"]
            )
            .first()
        )
        if existing is None:
            db.add(RiskClassification(
                id=uuid.uuid4(),
                action_pattern=item["action_pattern"],
                risk_level=item["risk_level"],
                enabled=True,
                created_at=now,
                updated_at=now,
            ))

    # Seed policy rules
    for item in POLICY_RULES:
        existing = (
            db.query(PolicyRule)
            .filter(
                PolicyRule.autonomy_level == item["autonomy_level"],
                PolicyRule.risk_level == item["risk_level"],
            )
            .first()
        )
        if existing is None:
            db.add(PolicyRule(
                id=uuid.uuid4(),
                autonomy_level=item["autonomy_level"],
                risk_level=item["risk_level"],
                decision=item["decision"],
                enabled=True,
                created_at=now,
                updated_at=now,
            ))

    db.commit()
    print(f"Seeded {len(RISK_CLASSIFICATIONS)} risk classifications")
    print(f"Seeded {len(POLICY_RULES)} policy rules")
