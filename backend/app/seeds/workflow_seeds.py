"""Seed workflow definitions for all platform chains.

One row per step, idempotent by (workflow_code, position).
Workflows defined here:
  procure_to_pay     — P1 → approval → S1 → P4 → F3 (5 steps)
  campaign_lifecycle — K1 → K2 → K3 → K5 → K6 (5 steps)
  amc_renewal        — V2 check → V2 renew → V2 register (3 steps)
  scm_chain          — C1 plan → C1 submit → C2 record → C3 intake (4 steps)
  hr_lifecycle       — H1 intake → H2 onboard → H3 score → H4 validate (4 steps)
"""

import logging

from sqlalchemy.orm import Session

from app.models.workflow import WorkflowStep


logger = logging.getLogger(__name__)


PROCURE_TO_PAY_STEPS = [
    (
        1,
        "purchase_intake",
        "purchase_request",
        "Validate the request, check budget, raise the purchase order",
    ),
    (
        2,
        "approval",
        None,
        "Approval gate — passes when no approval is outstanding",
    ),
    (
        3,
        "goods_receipt",
        "grn_request",
        "Receive the goods against the stored PO; serials captured",
    ),
    (
        4,
        "invoice_matching",
        "invoice_matching",
        "Three-way match of invoice against PO and GRN",
    ),
    (
        5,
        "tally_post",
        "books_to_tally",
        "Post the matched invoice to the books and stage Tally XML",
    ),
]


CAMPAIGN_LIFECYCLE_STEPS = [
    (1, "campaign_brief",    "K1", "Create campaign brief: name, type, SKUs, budget, dates"),
    (2, "audience_build",    "K2", "Build audience from DealerMart by branch + payment score"),
    (3, "broadcast",         "K3", "Broadcast WhatsApp messages via meta-approved template"),
    (4, "response_tracking", "K5", "Classify inbound responses; surface hot leads to Sales"),
    (5, "analytics",         "K6", "Compute delivery, response, and conversion KPIs"),
]

AMC_RENEWAL_STEPS = [
    (1, "check_expiry",   "V2", "Check contract expiry; flag if ≤ 90 days to renewal"),
    (2, "renew_contract", "V2", "Generate renewal proposal (NEEDS_APPROVAL)"),
    (3, "register",       "V2", "Register approved renewal contract in the platform"),
]

SCM_CHAIN_STEPS = [
    (1, "indent_plan",     "C1", "Plan replenishment indents from SkuHealth weeks-cover"),
    (2, "indent_submit",   "C1", "Submit approved indents to Purchase (NEEDS_APPROVAL)"),
    (3, "dispatch_record", "C2", "Record Samsung→Yexis or Yexis→Dealer dispatch + POD"),
    (4, "claim_intake",    "C3", "Raise SCM claim for short supply, damage, or price diff"),
]

HR_LIFECYCLE_STEPS = [
    (1, "job_intake",       "H1", "Open job requisition; draft JD for HR Manager review"),
    (2, "onboarding_start", "H2", "Create 10-item onboarding checklist for new joiner"),
    (3, "attrition_score",  "H3", "Score attrition risk: tenure + engagement signals"),
    (4, "payroll_validate", "H4", "Pre-payment payroll anomaly check before salary run"),
]


def _seed_workflow(db: Session, code: str, steps: list[tuple]) -> int:
    created = 0
    for position, step_key, task_type, description in steps:
        exists = db.query(WorkflowStep.id).filter_by(
            workflow_code=code,
            position=position,
        ).first()
        if exists is not None:
            continue
        db.add(WorkflowStep(
            workflow_code=code,
            position=position,
            step_key=step_key,
            task_type=task_type,
            description=description,
            params={},
        ))
        created += 1
    return created


def seed_workflows(db: Session) -> None:
    """Insert missing definition rows. Never alters existing ones."""
    total = 0

    workflows = [
        ("procure_to_pay",     PROCURE_TO_PAY_STEPS),
        ("campaign_lifecycle", CAMPAIGN_LIFECYCLE_STEPS),
        ("amc_renewal",        AMC_RENEWAL_STEPS),
        ("scm_chain",          SCM_CHAIN_STEPS),
        ("hr_lifecycle",       HR_LIFECYCLE_STEPS),
    ]

    for code, steps in workflows:
        total += _seed_workflow(db, code, steps)

    if total:
        db.commit()
        logger.info("Seeded %d workflow steps across %d workflows", total, len(workflows))
