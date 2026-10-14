"""D1 Ask-the-Business — curated mart read-tool set.

These functions are the *only* approved data path from marts to the D1
agent. Every function:
  • queries the local DB only (no live Zoho/Tally calls)
  • returns a typed dict the agent can render without further math
  • never performs division without a zero-guard
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.enterprise import (
    AmcContract,
    Campaign,
    DealerMart,
    DispatchRecord,
    HrEmployee,
    InstalledBase,
    KpiSnapshot,
    ScmClaim,
    SkuHealth,
    VendorScorecard,
)


def read_dealer_outstanding(
    db: Session,
    branch: str | None = None,
    top_n: int = 20,
) -> list[dict]:
    """All open dealer balances, optionally filtered by branch."""
    q = db.query(DealerMart)
    if branch:
        q = q.filter(DealerMart.branch == branch)
    rows = q.order_by(DealerMart.outstanding_amt.desc()).limit(top_n).all()
    return [
        {
            "dealer_ref": r.dealer_ref,
            "branch": r.branch,
            "outstanding_amt": r.outstanding_amt,
            "overdue_buckets": r.overdue_buckets_json or {},
            "payment_score": r.payment_behavior_score,
            "last_order_ts": r.last_order_ts.isoformat() if r.last_order_ts else None,
        }
        for r in rows
    ]


def read_overdue_dealers(
    db: Session,
    min_overdue_days: int = 0,
    branch: str | None = None,
    top_n: int = 10,
) -> list[dict]:
    """Dealers with overdue buckets, sorted by total overdue amount."""
    q = db.query(DealerMart).filter(DealerMart.overdue_buckets_json.isnot(None))
    if branch:
        q = q.filter(DealerMart.branch == branch)
    rows = q.limit(top_n * 5).all()  # over-fetch then filter

    results = []
    for r in rows:
        buckets = r.overdue_buckets_json or {}
        overdue_total = sum(
            v for k, v in buckets.items() if k not in ("current",)
        )
        if overdue_total <= 0:
            continue
        results.append({
            "dealer_ref": r.dealer_ref,
            "branch": r.branch,
            "overdue_total": round(overdue_total, 2),
            "buckets": buckets,
            "payment_score": r.payment_behavior_score,
        })

    results.sort(key=lambda x: x["overdue_total"], reverse=True)
    return results[:top_n]


def read_dealer_card(db: Session, dealer_ref: str) -> dict | None:
    """Full mart card for one dealer."""
    r = db.query(DealerMart).filter(DealerMart.dealer_ref == dealer_ref).first()
    if r is None:
        return None
    return {
        "dealer_ref": r.dealer_ref,
        "branch": r.branch,
        "outstanding_amt": r.outstanding_amt,
        "overdue_buckets": r.overdue_buckets_json or {},
        "payment_score": r.payment_behavior_score,
        "last_order_ts": r.last_order_ts.isoformat() if r.last_order_ts else None,
        "offtake_13wk": r.offtake_13wk_json,
        "scheme_eligibility": r.scheme_eligibility_json,
        "stock_opportunity": r.stock_opportunity_json,
        "refreshed_ts": r.refreshed_ts.isoformat() if r.refreshed_ts else None,
    }


def read_sku_position(
    db: Session,
    sku_ref: str,
    period: date | None = None,
) -> list[dict]:
    """Stock position for one SKU across all branches."""
    period = period or date.today()
    rows = (
        db.query(SkuHealth)
        .filter(SkuHealth.sku_ref == sku_ref, SkuHealth.period == period)
        .all()
    )
    return [
        {
            "sku_ref": r.sku_ref,
            "branch": r.branch,
            "on_hand": r.on_hand,
            "weeks_cover": r.weeks_cover,
            "ageing_buckets": r.ageing_buckets_json or {},
            "eol_flag": r.eol_flag,
            "action_reco": r.action_reco,
        }
        for r in rows
    ]


def read_sku_actions(
    db: Session,
    period: date | None = None,
    action_filter: str | None = None,
) -> list[dict]:
    """SKUs needing attention — optionally filtered by action type."""
    period = period or date.today()
    q = db.query(SkuHealth).filter(SkuHealth.period == period)
    if action_filter:
        q = q.filter(SkuHealth.action_reco == action_filter)
    else:
        q = q.filter(SkuHealth.action_reco != "ok").filter(
            SkuHealth.action_reco.isnot(None)
        )
    rows = q.order_by(SkuHealth.on_hand.desc()).all()
    return [
        {
            "sku_ref": r.sku_ref,
            "branch": r.branch,
            "on_hand": r.on_hand,
            "eol_flag": r.eol_flag,
            "action_reco": r.action_reco,
            "ageing_buckets": r.ageing_buckets_json or {},
        }
        for r in rows
    ]


def read_vendor_card(
    db: Session,
    vendor_ref: str,
    period: date | None = None,
) -> dict | None:
    """OTIF scorecard for one vendor."""
    period = period or date.today()
    r = (
        db.query(VendorScorecard)
        .filter(VendorScorecard.vendor_ref == vendor_ref, VendorScorecard.period == period)
        .first()
    )
    if r is None:
        return None
    return {
        "vendor_ref": r.vendor_ref,
        "period": str(r.period),
        "total_events": r.total_events,
        "on_time_pct": r.on_time_pct,
        "full_qty_pct": r.full_qty_pct,
        "otif_pct": r.otif_pct,
        "avg_delay_days": r.avg_delay_days,
        "refreshed_ts": r.refreshed_ts.isoformat() if r.refreshed_ts else None,
    }


def read_kpi(
    db: Session,
    kpi_id: str,
    period: date | None = None,
    entity: str = "YEXIS-EL",
    branch: str = "*",
) -> dict | None:
    """Single KPI snapshot lookup."""
    period = period or date.today()
    r = (
        db.query(KpiSnapshot)
        .filter(
            KpiSnapshot.kpi_id == kpi_id,
            KpiSnapshot.period == period,
            KpiSnapshot.entity == entity,
            KpiSnapshot.branch == branch,
        )
        .first()
    )
    if r is None:
        return None
    return {
        "kpi_id": r.kpi_id,
        "period": str(r.period),
        "entity": r.entity,
        "branch": r.branch,
        "value": r.value,
        "target": r.target,
        "baseline": r.baseline,
        "source_lane": r.source_lane,
        "computed_ts": r.computed_ts.isoformat() if r.computed_ts else None,
    }


def read_branch_kpis(
    db: Session,
    period: date | None = None,
    entity: str = "YEXIS-EL",
) -> dict[str, dict]:
    """All KPI snapshots for the given period, grouped by branch."""
    period = period or date.today()
    rows = (
        db.query(KpiSnapshot)
        .filter(KpiSnapshot.period == period, KpiSnapshot.entity == entity)
        .all()
    )
    grouped: dict[str, dict] = {}
    for r in rows:
        branch = r.branch or "*"
        grouped.setdefault(branch, {})[r.kpi_id] = r.value
    return grouped


def read_installed_base(
    db: Session,
    customer_ref: str | None = None,
    product_ref: str | None = None,
    status: str = "active",
) -> list[dict]:
    """Assets from installed_base, optionally filtered."""
    q = db.query(InstalledBase).filter(InstalledBase.status == status)
    if customer_ref:
        q = q.filter(InstalledBase.customer_ref == customer_ref)
    if product_ref:
        q = q.filter(InstalledBase.product_ref == product_ref)
    rows = q.limit(100).all()
    return [
        {
            "asset_id": r.asset_id,
            "serial": r.serial,
            "customer_ref": r.customer_ref,
            "product_ref": r.product_ref,
            "install_ts": r.install_ts.isoformat() if r.install_ts else None,
            "warranty_end": str(r.warranty_end) if r.warranty_end else None,
            "amc_contract_ref": r.amc_contract_ref,
            "amc_end": str(r.amc_end) if r.amc_end else None,
            "status": r.status,
            "last_service_ts": r.last_service_ts.isoformat() if r.last_service_ts else None,
            "fault_count_12m": r.fault_count_12m,
        }
        for r in rows
    ]


def read_amc_expiry(
    db: Session,
    within_days: int = 90,
    today: date | None = None,
) -> list[dict]:
    """AMC contracts expiring within N days (from InstalledBase spine)."""
    today = today or date.today()
    cutoff = today + timedelta(days=within_days)
    rows = (
        db.query(InstalledBase)
        .filter(InstalledBase.amc_end.isnot(None))
        .filter(InstalledBase.amc_end >= today)
        .filter(InstalledBase.amc_end <= cutoff)
        .order_by(InstalledBase.amc_end)
        .all()
    )
    return [
        {
            "asset_id": r.asset_id,
            "serial": r.serial,
            "customer_ref": r.customer_ref,
            "product_ref": r.product_ref,
            "amc_end": str(r.amc_end),
            "days_to_expiry": (r.amc_end - today).days,
        }
        for r in rows
    ]


def read_amc_contracts(
    db: Session,
    entity: str = "YEXIS-EL",
    status: str | None = None,
    within_days: int | None = None,
    today: date | None = None,
) -> list[dict]:
    """Active AMC contracts from the V2 AmcContract table.

    Pass status to filter (active|expired|renewed|cancelled).
    Pass within_days to limit to contracts expiring within that window.
    """
    today = today or date.today()
    q = db.query(AmcContract).filter(AmcContract.entity == entity)
    if status:
        q = q.filter(AmcContract.status == status)
    if within_days is not None:
        cutoff = today + timedelta(days=within_days)
        q = q.filter(AmcContract.end_date >= today, AmcContract.end_date <= cutoff)
    rows = q.order_by(AmcContract.end_date).limit(100).all()
    return [
        {
            "contract_ref": r.contract_ref,
            "asset_id": r.asset_id,
            "customer_ref": r.customer_ref,
            "entity": r.entity,
            "start_date": str(r.start_date),
            "end_date": str(r.end_date),
            "days_to_expiry": (r.end_date - today).days,
            "value": r.value,
            "status": r.status,
            "renewal_ref": r.renewal_ref,
        }
        for r in rows
    ]


def read_campaign_summary(
    db: Session,
    entity: str = "YEXIS-EL",
    status: str | None = None,
    top_n: int = 10,
) -> list[dict]:
    """Campaign list with K6 analytics from Campaign.kpi_json."""
    q = db.query(Campaign).filter(Campaign.entity == entity)
    if status:
        q = q.filter(Campaign.status == status)
    rows = q.order_by(Campaign.created_ts.desc()).limit(top_n).all()
    return [
        {
            "campaign_ref": r.campaign_ref,
            "name": r.name,
            "campaign_type": r.campaign_type,
            "status": r.status,
            "start_date": str(r.start_date) if r.start_date else None,
            "end_date": str(r.end_date) if r.end_date else None,
            "budget": r.budget,
            "kpi": r.kpi_json or {},
        }
        for r in rows
    ]


def read_dispatch_status(
    db: Session,
    entity: str = "YEXIS-EL",
    status: str | None = None,
    overdue_only: bool = False,
    today: date | None = None,
) -> list[dict]:
    """In-transit and overdue dispatches from C2 DispatchRecord."""
    today = today or date.today()
    q = db.query(DispatchRecord).filter(DispatchRecord.entity == entity)
    if overdue_only:
        q = q.filter(
            DispatchRecord.status.in_(["dispatched", "in_transit"]),
            DispatchRecord.expected_delivery < today,
        )
    elif status:
        q = q.filter(DispatchRecord.status == status)
    else:
        q = q.filter(DispatchRecord.status.in_(["dispatched", "in_transit"]))
    rows = q.order_by(DispatchRecord.expected_delivery).limit(100).all()
    return [
        {
            "dispatch_ref": r.dispatch_ref,
            "dispatch_type": r.dispatch_type,
            "from_ref": r.from_ref,
            "to_ref": r.to_ref,
            "qty": r.qty,
            "status": r.status,
            "dispatch_ts": r.dispatch_ts.isoformat() if r.dispatch_ts else None,
            "expected_delivery": str(r.expected_delivery) if r.expected_delivery else None,
            "pod_ts": r.pod_ts.isoformat() if r.pod_ts else None,
            "overdue_days": (
                (today - r.expected_delivery).days
                if r.expected_delivery and r.expected_delivery < today and not r.pod_ts
                else 0
            ),
        }
        for r in rows
    ]


def read_scm_claims(
    db: Session,
    entity: str = "YEXIS-EL",
    status: str | None = None,
    claim_type: str | None = None,
    top_n: int = 50,
) -> list[dict]:
    """Open / filtered SCM claims from C3 ScmClaim."""
    q = db.query(ScmClaim).filter(ScmClaim.entity == entity)
    if status:
        q = q.filter(ScmClaim.status == status)
    else:
        q = q.filter(ScmClaim.status.notin_(["settled", "rejected"]))
    if claim_type:
        q = q.filter(ScmClaim.claim_type == claim_type)
    rows = q.order_by(ScmClaim.raised_ts.desc()).limit(top_n).all()
    return [
        {
            "claim_ref": r.claim_ref,
            "claim_type": r.claim_type,
            "counterparty_ref": r.counterparty_ref,
            "invoice_ref": r.invoice_ref,
            "amount_claimed": r.amount_claimed,
            "amount_settled": r.amount_settled,
            "status": r.status,
            "raised_ts": r.raised_ts.isoformat() if r.raised_ts else None,
            "submitted_ts": r.submitted_ts.isoformat() if r.submitted_ts else None,
        }
        for r in rows
    ]


def read_attrition_risk(
    db: Session,
    entity: str = "YEXIS-EL",
    tier: str | None = None,
) -> list[dict]:
    """H3 attrition risk scores for active employees.

    tier: 'High' (≥60), 'Medium' (30-59), 'Low' (<30).
    Returns up to 50 employees sorted by risk_score descending.
    """
    rows = (
        db.query(HrEmployee)
        .filter(HrEmployee.entity == entity, HrEmployee.status == "active")
        .all()
    )
    results = []
    for r in rows:
        score = r.risk_score
        if score is None:
            tier_label = "Unknown"
        elif score >= 60:
            tier_label = "High"
        elif score >= 30:
            tier_label = "Medium"
        else:
            tier_label = "Low"

        if tier and tier_label != tier:
            continue
        results.append({
            "employee_id": r.employee_id,
            "name": r.name,
            "department": r.department,
            "role": r.role,
            "risk_score": score,
            "risk_tier": tier_label,
            "last_engagement_ts": (
                r.last_engagement_ts.isoformat() if r.last_engagement_ts else None
            ),
        })

    results.sort(key=lambda x: (x["risk_score"] or 0), reverse=True)
    return results[:50]
