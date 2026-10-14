"""Seed the agents table from the registered implementation set.

The executor refuses to run a task whose agent has no ``agents`` row,
so a fresh database needs these before anything works. Idempotent by
``agent_code``: existing rows are never altered — autonomy is earned
through promotion gates, never reset by a boot.

``enabled=True`` on seed: disabling an agent is an operational
decision taken through the API or directly in the database, and a
restart must not silently re-enable it.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.agent import Agent


logger = logging.getLogger(__name__)


# code → (name, suite, autonomy, version, description)
AGENT_DEFAULTS = {
    "P1": (
        "Purchase Intake",
        "Purchase",
        "A1",
        "v0.1.0",
        "Purchase request intake and PO workflow",
    ),
    "P2": (
        "Sourcing RFQ",
        "Purchase",
        "A1",
        "v0.1.0",
        "Sourcing/RFQ: issue RFQs, normalise quotes, recommend award "
        "for buyer approval",
    ),
    "P4": (
        "Invoice Matching",
        "Purchase",
        "A1",
        "v0.1.0",
        "Three-way invoice match against PO and GRN",
    ),
    "S1": (
        "GRN and Serialisation",
        "Stores",
        "A1",
        "v0.1.0",
        "Goods receipt with serial-number capture",
    ),
    "S3": (
        "Reorder",
        "Stores",
        "A1",
        "v0.1.0",
        "Deterministic reorder-point proposals feeding P1",
    ),
    "S2": (
        "Stock Health",
        "Stores",
        "A1",
        "v0.1.0",
        "Weekly ageing/EOL scoring and stock-health brief",
    ),
    "P3": (
        "Expediting",
        "Purchase",
        "A1",
        "v0.1.0",
        "Vendor follow-up on open POs: confirmations, slippage chases, "
        "impact triage and mitigation proposals",
    ),
    "F1": (
        "Collections",
        "Finance",
        "A1",
        "v0.1.0",
        "Receivables collections reminders and promise tracking",
    ),
    "F3": (
        "Books to Tally",
        "Finance",
        "A1",
        "v0.1.0",
        "Period posting from books to Tally via stage-and-approve",
    ),
    "D1": (
        "Ask the Business",
        "Dashboard",
        "A1",
        "v0.1.0",
        "Read-only business analytics queries",
    ),
    "V1": (
        "Service Desk",
        "Service",
        "A1",
        "v0.1.0",
        "Customer service request intake and dispatch",
    ),
    "F2": (
        "Scheme Claims",
        "Finance",
        "A2",
        "v1.0.0",
        "Samsung scheme claim lifecycle: intake, validate, and submit to channel",
    ),
    "F4": (
        "Interentity Recon",
        "Finance",
        "A2",
        "v1.0.0",
        "Reconcile interentity transactions between YEXIS-EL and YEXIS-SL",
    ),
    "F5": (
        "Cash Forecast",
        "Finance",
        "A1",
        "v1.0.0",
        "13-week rolling cash forecast from receivables and payables",
    ),
    "H1": (
        "Recruitment",
        "HR",
        "A1",
        "v1.0.0",
        "Job opening intake, JD drafting, and recruitment pipeline tracking",
    ),
    "H2": (
        "Onboarding",
        "HR",
        "A2",
        "v1.0.0",
        "Employee onboarding checklist creation and completion tracking",
    ),
    "H3": (
        "Attrition Risk",
        "HR",
        "A1",
        "v1.0.0",
        "Employee attrition risk scoring based on tenure and engagement signals",
    ),
    "H4": (
        "Payroll Check",
        "HR",
        "A1",
        "v1.0.0",
        "Pre-payment payroll anomaly detection: ghost employees, duplicates, outliers",
    ),
    "S4": (
        "Physical Verification",
        "Stores",
        "A2",
        "v1.0.0",
        "Serial-level physical count reconciliation against the serial registry",
    ),
    "V2": (
        "AMC Engine",
        "Service",
        "A2",
        "v1.0.0",
        "AMC contract lifecycle: expiry alerts, renewal proposals, contract registration",
    ),
    "C1": (
        "Indent Planning",
        "SCM",
        "A1",
        "v1.0.0",
        "Demand-driven indent suggestions from SkuHealth weeks-cover data",
    ),
    "C2": (
        "Dispatch Tracker",
        "SCM",
        "A2",
        "v1.0.0",
        "Samsung→Yexis and Yexis→dealer dispatch recording and POD tracking",
    ),
    "C3": (
        "SCM Claims Tracker",
        "SCM",
        "A2",
        "v1.0.0",
        "SCM claim lifecycle: short-supply, damage, price-diff, scheme, logistics",
    ),
    "K1": (
        "Campaign Brief",
        "Campaigns",
        "A1",
        "v1.0.0",
        "Campaign brief creation and status management for the K-suite",
    ),
    "K2": (
        "Audience Builder",
        "Campaigns",
        "A2",
        "v1.0.0",
        "Audience segmentation from DealerMart by branch and payment score",
    ),
    "K3": (
        "Broadcast",
        "Campaigns",
        "A3",
        "v1.0.0",
        "WhatsApp broadcast to campaign audience using meta-approved templates",
    ),
    "K4": (
        "Dealer Promo",
        "Campaigns",
        "A1",
        "v1.0.0",
        "Incentive eligibility computation from outstanding and payment behavior",
    ),
    "K5": (
        "Response Tracker",
        "Campaigns",
        "A2",
        "v1.0.0",
        "Campaign response classification and hot-lead surfacing",
    ),
    "K6": (
        "Campaign Analytics",
        "Campaigns",
        "A1",
        "v1.0.0",
        "Campaign KPI computation: delivery, response, conversion, and hot-lead rates",
    ),
    "M1": (
        "ISM Command",
        "ISM",
        "A1",
        "v1.0.0",
        "Internal Sales Manager command centre: pipeline, targets, visits, alerts",
    ),
    "M2": (
        "GSM Command",
        "GSM",
        "A1",
        "v1.0.0",
        "General/Senior Sales Manager territory command centre: scorecards, KPIs, topline",
    ),
}


def seed_agents(db: Session) -> None:
    now = datetime.now(timezone.utc)
    created = 0

    for code, (name, suite, autonomy, version, description) in (
        AGENT_DEFAULTS.items()
    ):
        exists = (
            db.query(Agent.id)
            .filter(Agent.agent_code == code)
            .first()
        )

        if exists is not None:
            continue

        db.add(
            Agent(
                id=uuid.uuid4(),
                agent_code=code,
                name=name,
                suite=suite,
                autonomy_level=autonomy,
                version=version,
                description=description,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1

    if created:
        db.commit()
        logger.info("Seeded %d agent rows", created)
