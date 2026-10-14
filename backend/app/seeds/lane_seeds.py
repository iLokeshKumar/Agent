"""Seed lane configs — one governed lane per registered agent.

Defaults are deliberately conservative: every lane starts A1
(propose-only), no cap. Autonomy is earned through promotion gates,
never granted at seed time.
"""

import logging

from sqlalchemy.orm import Session

from app.models.lane_config import LaneConfig


logger = logging.getLogger(__name__)

# The Phase-1 go-live set, plus P2 (Phase-2, first agent) and S3
# (Stores automation set). New agents ship A1: propose-only until they
# earn promotion through the gates.
LANE_DEFAULTS = {
    "P1": {"mode": "hybrid"},
    "P2": {"mode": "hybrid", "notes": "buyer always awards the RFQ"},
    "P3": {"mode": "hybrid", "notes": "chases run A2 once promoted; spend always human"},
    "P4": {"mode": "headless"},
    "S1": {"mode": "headless"},
    "S2": {"mode": "supervised", "notes": "read-only brief; feeds S3"},
    "S3": {"mode": "supervised", "notes": "proposals only; P1 raises the PO"},
    "F1": {"mode": "hybrid", "notes": "stop-supply always human-decided"},
    "F3": {"mode": "hybrid", "notes": "nothing posts without accountant approval"},
    "V1": {"mode": "hybrid"},
    "D1": {"mode": "supervised", "notes": "read-only analytics"},
    "F2": {"mode": "hybrid", "notes": "claim submit always NEEDS_APPROVAL until promoted"},
    "F4": {"mode": "supervised", "notes": "recon proposals only; ops reviews mismatches"},
    "F5": {"mode": "supervised", "notes": "forecast is advisory; treasury approves draw-down"},
    "H1": {"mode": "supervised", "notes": "JD drafts require HR Manager review before posting"},
    "H2": {"mode": "hybrid", "notes": "onboarding completion requires HR sign-off"},
    "H3": {"mode": "supervised", "notes": "risk scores are advisory; HR Manager acts"},
    "H4": {"mode": "supervised", "notes": "HIGH anomalies block payroll run; human resolves"},
    "S4": {"mode": "hybrid", "notes": "reconciliation requires store manager approval"},
    "V2": {"mode": "hybrid", "notes": "renewal proposals NEEDS_APPROVAL; ops confirms"},
    "C1": {"mode": "supervised", "notes": "indent suggestions always submitted for buyer approval"},
    "C2": {"mode": "hybrid", "notes": "dispatch recording autonomous; POD updates supervised"},
    "C3": {"mode": "hybrid", "notes": "settlement always NEEDS_APPROVAL; ops reviews"},
    "K1": {"mode": "supervised", "notes": "campaign brief requires marketing approval before audience build"},
    "K2": {"mode": "hybrid", "notes": "audience build autonomous within branch+score filter"},
    "K3": {"mode": "headless", "notes": "broadcast A3 — meta-approved templates only; 200 cap"},
    "K4": {"mode": "supervised", "notes": "incentive disbursement always NEEDS_APPROVAL"},
    "K5": {"mode": "hybrid", "notes": "response classification autonomous; hot-lead routing supervised"},
    "K6": {"mode": "supervised", "notes": "analytics advisory; campaign close requires ops sign-off"},
    "M1": {"mode": "supervised", "notes": "read-only command centre; ISM acts via other agents"},
    "M2": {"mode": "supervised", "notes": "read-only command centre; GM acts via other agents"},
}


def seed_lanes(db: Session) -> None:
    created = 0

    for lane, defaults in LANE_DEFAULTS.items():
        exists = db.query(LaneConfig.id).filter_by(lane=lane).first()
        if exists is not None:
            continue

        db.add(LaneConfig(
            lane=lane,
            autonomy_state="A1",
            kill_switch=False,
            dry_run=False,
            daily_cap=None,
            **defaults,
        ))
        created += 1

    if created:
        db.commit()
        logger.info("Seeded %d lane configs", created)
