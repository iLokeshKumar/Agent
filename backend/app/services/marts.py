"""Nightly mart & KPI builders — WP-8 compute (real, deterministic).

Reads local operational state and the Zoho sync cache (the local stand-in
for Books/CRM extracts until the cloud store exists) and upserts:

  dealer_mart       — per-dealer outstanding, overdue buckets, promise score
  sku_health        — per SKU+branch on-hand from serial_registry, ageing,
                      EOL flag, action recommendation
  vendor_scorecard  — per-vendor OTIF aggregated from scorecard events
  installed_base    — sold serials mirrored from serial_registry + invoice cache
  kpi_snapshot      — 20 KPIs across Finance/Stores/SCM/HR/Service/Command

Everything is computed in Python over simple queries/JSON caches —
portable across Postgres and the SQLite contract tests, and auditable:
every snapshot row names the lane that produced it.
"""

import logging
from datetime import date, datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.models.enterprise import (
    AmcContract,
    Campaign,
    DealerMart,
    DispatchRecord,
    HrEmployee,
    InstalledBase,
    KpiSnapshot,
    MessageLog,
    PromiseRecord,
    Requisition,
    ScmClaim,
    SerialRegistry,
    SkuHealth,
    TbReconciliation,
    VendorScorecard,
    VendorScorecardEvent,
)
from app.services.stock_health import action_reco as stock_action_reco
from app.sync.cache import SyncCache

logger = logging.getLogger(__name__)

KPI_SOURCE_LANE = "nightly_kpi"
MART_SOURCE_LANE = "nightly_marts"

# Ageing buckets (days since receipt) — S2 spec thresholds.
AGEING_BUCKETS = {"0_30": 30, "31_60": 60, "61_90": 90, "over_90": None}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── dealer_mart ───────────────────────────────────────────────────


def _build_offtake_map(invoice_lines: list[dict]) -> dict[str, dict]:
    """Aggregate invoice line items into per-dealer per-SKU offtake.

    Returns: {customer_id: {"skus": {item_id: {"qty": N, "value": V}}, "total_value": V}}
    """
    dealer_offtake: dict[str, dict] = {}

    for inv in invoice_lines:
        cid = inv.get("customer_id") or inv.get("customer_name") or ""
        if not cid:
            continue
        if cid not in dealer_offtake:
            dealer_offtake[cid] = {"skus": {}, "total_value": 0.0}

        for line in inv.get("lines", []):
            item_id = line.get("item_id") or line.get("name") or ""
            if not item_id:
                continue
            qty = float(line.get("quantity") or 0)
            value = float(line.get("item_total") or 0)

            skus = dealer_offtake[cid]["skus"]
            if item_id not in skus:
                skus[item_id] = {"qty": 0.0, "value": 0.0}
            skus[item_id]["qty"] += qty
            skus[item_id]["value"] += value
            dealer_offtake[cid]["total_value"] += value

    # Round for clean JSON storage
    for entry in dealer_offtake.values():
        entry["total_value"] = round(entry["total_value"], 2)
        for sku in entry["skus"].values():
            sku["qty"] = round(sku["qty"], 4)
            sku["value"] = round(sku["value"], 2)

    return dealer_offtake


def build_dealer_mart(db: Session, cache: SyncCache | None = None) -> dict:
    """Join Books/CRM cache + local promise history into dealer_mart."""
    cache = cache or SyncCache()
    invoices = cache.get_data("invoices")
    contacts = cache.get_data("contacts")
    invoice_lines = cache.get_data("invoice_line_items_13wk")
    promises = db.query(PromiseRecord).all()

    today = _utcnow().date()

    contact_branch = {
        c.get("contact_id", ""): c.get("company_name") or ""
        for c in contacts
    }

    # Per-customer outstanding aggregation from the invoice extract.
    dealers: dict[str, dict] = {}

    def _row(ref: str) -> dict:
        return dealers.setdefault(ref, {
            "outstanding": 0.0,
            "overdue": 0.0,
            "buckets": {"current": 0.0, "d1_30": 0.0, "d31_60": 0.0,
                        "d61_90": 0.0, "over_90": 0.0},
            "last_order": None,
        })

    for inv in invoices:
        ref = inv.get("customer_id") or inv.get("customer_name") or ""
        if not ref:
            continue
        row = _row(ref)
        balance = float(inv.get("balance") or 0)
        row["outstanding"] += balance

        due = inv.get("due_date") or ""
        try:
            due_date = date.fromisoformat(str(due)[:10])
            overdue_days = (today - due_date).days
        except ValueError:
            overdue_days = 0

        if balance > 0 and overdue_days > 0:
            row["overdue"] += balance
            if overdue_days <= 30:
                row["buckets"]["d1_30"] += balance
            elif overdue_days <= 60:
                row["buckets"]["d31_60"] += balance
            elif overdue_days <= 90:
                row["buckets"]["d61_90"] += balance
            else:
                row["buckets"]["over_90"] += balance
        else:
            row["buckets"]["current"] += balance

        inv_date = inv.get("date") or ""
        try:
            d = date.fromisoformat(str(inv_date)[:10])
            if row["last_order"] is None or d > row["last_order"]:
                row["last_order"] = d
        except ValueError:
            pass

    # Promise reliability per account from F1 records.
    by_account: dict[str, dict] = {}
    for p in promises:
        acc = p.account_ref or ""
        stats = by_account.setdefault(acc, {"kept": 0, "broken": 0})
        if p.outcome == "kept":
            stats["kept"] += 1
        elif p.outcome == "broken":
            stats["broken"] += 1

    offtake_map = _build_offtake_map(invoice_lines)

    upserted = 0
    for ref, row in dealers.items():
        stats = by_account.get(ref) or by_account.get(
            contact_branch.get(ref, "")
        ) or {}
        total_promises = stats.get("kept", 0) + stats.get("broken", 0)
        score = (
            round(stats["kept"] / total_promises, 4)
            if total_promises else None
        )

        offtake = offtake_map.get(ref)

        db.merge(DealerMart(
            dealer_ref=ref,
            branch=contact_branch.get(ref) or None,
            outstanding_amt=row["outstanding"],
            overdue_buckets_json=row["buckets"],
            payment_behavior_score=score,
            last_order_ts=(
                datetime.combine(row["last_order"], datetime.min.time())
                if row["last_order"] else None
            ),
            offtake_13wk_json=offtake,
            refreshed_ts=_utcnow(),
        ))
        upserted += 1

    db.commit()
    return {"mart": "dealer_mart", "upserted": upserted,
            "source_lane": MART_SOURCE_LANE}


# ── sku_health ────────────────────────────────────────────────────


def build_sku_health(
    db: Session,
    period: date | None = None,
    eol_skus: set[str] | None = None,
    cache: SyncCache | None = None,
) -> dict:
    """On-hand and ageing from serial_registry; EOL from caller policy.

    weeks_cover stays None until an offtake feed exists (honest absence
    beats a made-up number — the S2 brief renders it as "n/a").
    """
    period = period or _utcnow().date()
    eol_skus = eol_skus or set()
    cache = cache or SyncCache()
    today = period

    rows = db.query(SerialRegistry).all()

    grouped: dict[tuple[str, str], dict] = {}

    def _bucket(ref: str, branch: str) -> dict:
        key = (ref, branch)
        return grouped.setdefault(key, {
            "on_hand": 0,
            "ageing": {"0_30": 0, "31_60": 0, "61_90": 0, "over_90": 0},
        })

    for s in rows:
        if s.status not in ("in_stock", "reserved"):
            continue
        ref = s.sku_ref or ""
        branch = s.location_ref or "HO"
        b = _bucket(ref, branch)
        b["on_hand"] += 1

        base = s.received_ts or s.last_event_ts
        if base is None:
            continue
        if getattr(base, "tzinfo", None) is not None:
            base = base.replace(tzinfo=None)
        age_days = (datetime.combine(today, datetime.min.time())
                    - datetime.combine(base.date(), datetime.min.time())
                    ).days
        if age_days <= 30:
            b["ageing"]["0_30"] += 1
        elif age_days <= 60:
            b["ageing"]["31_60"] += 1
        elif age_days <= 90:
            b["ageing"]["61_90"] += 1
        else:
            b["ageing"]["over_90"] += 1

    upserted = 0
    for (ref, branch), agg in grouped.items():
        ageing = agg["ageing"]
        is_eol = ref in eol_skus

        # Shared verdict with the S2 agent — one decision rule, no drift.
        reco = stock_action_reco(
            eol=is_eol,
            on_hand=agg["on_hand"],
            ageing=ageing,
            weeks_cover=None,  # no offtake feed on this path yet
        )

        db.merge(SkuHealth(
            sku_ref=ref,
            branch=branch,
            period=period,
            on_hand=agg["on_hand"],
            weeks_cover=None,
            ageing_buckets_json=ageing,
            eol_flag=is_eol,
            action_reco=reco,
        ))
        upserted += 1

    db.commit()
    return {"mart": "sku_health", "upserted": upserted,
            "period": str(period), "source_lane": MART_SOURCE_LANE}


# ── vendor_scorecard ──────────────────────────────────────────────


def build_vendor_scorecard(
    db: Session,
    period: date | None = None,
    entity: str = "YEXIS-EL",
) -> dict:
    """Aggregate VendorScorecardEvent rows into per-vendor OTIF scores.

    Only `deliver` events contribute to on-time / full-qty metrics;
    `confirm` and `delay` events enrich the event count so the mart
    reflects the full P3 expediting picture.
    """
    period = period or _utcnow().date()

    events = db.query(VendorScorecardEvent).all()

    vendors: dict[str, dict] = {}

    def _v(ref: str) -> dict:
        return vendors.setdefault(ref, {
            "total": 0,
            "delivers": 0,
            "on_time": 0,
            "full_qty": 0,
            "delay_days": [],
        })

    for ev in events:
        ref = ev.vendor_ref or ""
        if not ref:
            continue
        v = _v(ref)
        v["total"] += 1

        if ev.event == "deliver":
            v["delivers"] += 1
            # on-time: actual_dt <= promised_dt
            if ev.actual_dt and ev.promised_dt:
                delay = (ev.actual_dt - ev.promised_dt).days
                v["delay_days"].append(delay)
                if delay <= 0:
                    v["on_time"] += 1
            # full-qty: qty_var is None (not recorded) or 0
            if ev.qty_var is None or ev.qty_var == 0:
                v["full_qty"] += 1

    upserted = 0
    for ref, v in vendors.items():
        d = v["delivers"]
        on_time_pct = round(v["on_time"] / d, 4) if d else None
        full_qty_pct = round(v["full_qty"] / d, 4) if d else None
        otif_pct = (
            round((v["on_time"] + v["full_qty"]) / (2 * d), 4)
            if d else None
        )
        avg_delay = (
            round(sum(v["delay_days"]) / len(v["delay_days"]), 2)
            if v["delay_days"] else None
        )

        db.merge(VendorScorecard(
            vendor_ref=ref,
            period=period,
            entity=entity,
            total_events=v["total"],
            on_time_pct=on_time_pct,
            full_qty_pct=full_qty_pct,
            otif_pct=otif_pct,
            avg_delay_days=avg_delay,
            refreshed_ts=_utcnow(),
        ))
        upserted += 1

    db.commit()
    return {"mart": "vendor_scorecard", "upserted": upserted,
            "period": str(period), "source_lane": MART_SOURCE_LANE}


# ── installed_base ────────────────────────────────────────────────


def build_installed_base(
    db: Session,
    cache: SyncCache | None = None,
) -> dict:
    """Mirror sold serials from serial_registry into installed_base.

    asset_id == serial (serials are globally unique per Samsung GS spec).
    Invoice cache provides customer_ref and sold_invoice_ref lookup.
    Warranty / AMC fields are left None until V1/V2 agents update them.
    """
    cache = cache or SyncCache()
    invoices = cache.get_data("invoices")

    # Build a lookup: invoice_ref → {customer_ref, customer_name}
    inv_map: dict[str, dict] = {}
    for inv in invoices:
        ref = inv.get("invoice_id") or inv.get("invoice_number") or ""
        if ref:
            inv_map[ref] = {
                "customer_ref": inv.get("customer_id") or inv.get("customer_name") or "",
            }

    sold = db.query(SerialRegistry).filter(
        SerialRegistry.status == "sold"
    ).all()

    upserted = 0
    for s in sold:
        inv_data = inv_map.get(s.sold_invoice_ref or "") or {}
        customer_ref = (
            inv_data.get("customer_ref")
            or s.custodian_ref
            or None
        )

        db.merge(InstalledBase(
            asset_id=s.serial,
            serial=s.serial,
            customer_ref=customer_ref,
            product_ref=s.sku_ref,
            sold_invoice_ref=s.sold_invoice_ref,
            install_ts=s.last_event_ts,
            status="active",
        ))
        upserted += 1

    db.commit()
    return {"mart": "installed_base", "upserted": upserted,
            "source_lane": MART_SOURCE_LANE}


# ── kpi_snapshot ──────────────────────────────────────────────────


def _kpi_set(
    db: Session,
    kpi_id: str,
    value: float | None,
    period: date,
    entity: str = "YEXIS-EL",
    branch: str = "*",
) -> None:
    """Idempotent per-period upsert of one KPI row."""
    if value is None:
        return
    db.merge(KpiSnapshot(
        kpi_id=kpi_id,
        period=period,
        entity=entity,
        branch=branch,
        value=round(float(value), 4),
        source_lane=KPI_SOURCE_LANE,
        computed_ts=_utcnow(),
    ))


def compute_kpis(
    db: Session,
    period: date | None = None,
    entity: str = "YEXIS-EL",
    cache: SyncCache | None = None,
) -> dict:
    """Nightly KPI board — 23 KPIs with source lineage.

    Formula key
    -----------
    dso_days                    avg(today - invoice_date) on zero-balance invoices
    p4_auto_match_rate          FULLY_MATCHED tasks / total invoice_matching tasks
    promise_reliability         kept / (kept+broken) PromiseRecords
    overdue_receivables         sum(balance) where due_date < today
    total_open_receivables      sum(balance) on all open invoices
    f3_books_tally_drift_hrs    sum(abs(drift_amount)) on non-clean TB rows (proxy for effort)
    f2_claim_accuracy_pct       approved scheme_claim tasks / total settled tasks × 100
    cash_forecast_accuracy      promises kept this month / total promises made this month × 100
    stock_availability_pct      in_stock+reserved serials / all active serials × 100
    serial_conservation_rate    (total - mystery_status) / total serials × 100
    reorder_cycle_days          avg days from Requisition.created_ts to po_ref set
    vendor_otif_pct             VendorScorecard.otif_pct aggregated (weighted avg)
    dispatch_to_pod_days        avg (pod_ts - dispatch_ts).days on C2 delivered dispatches
    requisition_cycle_hours     same as reorder_cycle_days but in hours (precision)
    time_to_fill_days           avg days on completed hire tasks (None if no data)
    amc_renewal_rate_pct        AmcContract: renewed / expiring_in_90d × 100 per entity
    revenue_per_fte             total_open_receivables / headcount from settings
    attrition_risk_score        mean H3 risk_score across active employees per entity
    talk_time_pct               outbound WA messages / total WA messages × 100
    pipeline_coverage_ratio     None — needs live CRM pipeline data
    scm_claim_resolution_days   avg (settled_ts - raised_ts).days on C3 settled claims
    campaign_delivery_rate_pct  avg delivery_rate_pct across closed K6 campaigns
    campaign_response_rate_pct  avg response_rate_pct across closed K6 campaigns
    """
    from app.models.task import Task

    cache = cache or SyncCache()
    period = period or _utcnow().date()
    today = period

    computed: dict[str, float | None] = {}

    # ── 1. DSO
    days = []
    for inv in cache.get_data("invoices"):
        try:
            inv_date = date.fromisoformat(str(inv.get("date", ""))[:10])
        except ValueError:
            continue
        if float(inv.get("balance") or 0) == 0:
            days.append((today - inv_date).days)
    computed["dso_days"] = round(sum(days) / len(days), 2) if days else None

    # ── 2. Promise reliability
    promises = db.query(PromiseRecord).all()
    kept = sum(1 for p in promises if p.outcome == "kept")
    broken = sum(1 for p in promises if p.outcome == "broken")
    computed["promise_reliability"] = (
        round(kept / (kept + broken), 4) if (kept + broken) else None
    )

    # ── 3. P4 auto-match rate
    inv_tasks = (
        db.query(Task)
        .filter(Task.task_type == "invoice_matching")
        .filter(Task.status == "SUCCESS")
        .all()
    )
    matched = sum(
        1
        for t in inv_tasks
        if ((t.result or {}).get("output") or {})
        .get("match_result", {})
        .get("match_status")
        == "FULLY_MATCHED"
    )
    computed["p4_auto_match_rate"] = (
        round(matched / len(inv_tasks), 4) if inv_tasks else None
    )

    # ── 4 & 5. Overdue / total open receivables
    overdue = 0.0
    total_open = 0.0
    for inv in cache.get_data("invoices"):
        balance = float(inv.get("balance") or 0)
        if balance <= 0:
            continue
        total_open += balance
        try:
            due = date.fromisoformat(str(inv.get("due_date"))[:10])
            if due < today:
                overdue += balance
        except ValueError:
            pass
    computed["overdue_receivables"] = round(overdue, 2)
    computed["total_open_receivables"] = round(total_open, 2)

    # ── 6. F3 Books↔Tally drift effort (sum of absolute drift on open rows)
    tb_rows = (
        db.query(TbReconciliation)
        .filter(TbReconciliation.entity == entity)
        .filter(TbReconciliation.status != "clean")
        .all()
    )
    computed["f3_books_tally_drift_hrs"] = (
        round(sum(abs(r.drift_amount) for r in tb_rows), 2)
        if tb_rows else 0.0
    )

    # ── 7. F2 claim accuracy: approved / (approved+rejected) scheme tasks
    claim_tasks = (
        db.query(Task)
        .filter(Task.task_type == "scheme_claim")
        .filter(Task.status.in_(["SUCCESS", "REJECTED"]))
        .all()
    )
    approved = sum(1 for t in claim_tasks if t.status == "SUCCESS")
    total_claims = len(claim_tasks)
    computed["f2_claim_accuracy_pct"] = (
        round(approved / total_claims * 100, 2) if total_claims else None
    )

    # ── 8. Cash forecast accuracy: promises kept this period / made this period
    month_start = today.replace(day=1)
    month_promises = [
        p for p in promises
        if p.made_ts and p.made_ts.date() >= month_start
    ]
    month_kept = sum(1 for p in month_promises if p.outcome == "kept")
    computed["cash_forecast_accuracy"] = (
        round(month_kept / len(month_promises) * 100, 2)
        if month_promises else None
    )

    # ── 9. Stock availability: in_stock+reserved / all non-sold serials
    serials = db.query(SerialRegistry).all()
    available = sum(1 for s in serials if s.status in ("in_stock", "reserved"))
    active = sum(1 for s in serials if s.status not in ("sold", "written_off", "rma"))
    computed["stock_availability_pct"] = (
        round(available / active * 100, 2) if active else None
    )

    # ── 10. Serial conservation: all statuses are known/expected
    _known_statuses = {
        "in_stock", "reserved", "issued_project", "demo_out",
        "sold", "rma", "returned", "written_off",
    }
    mystery = sum(1 for s in serials if s.status not in _known_statuses)
    total_serials = len(serials)
    computed["serial_conservation_rate"] = (
        round((total_serials - mystery) / total_serials * 100, 2)
        if total_serials else None
    )

    # ── 11 & 14. Reorder cycle (days) and requisition cycle (hours)
    reqs = (
        db.query(Requisition)
        .filter(Requisition.status == "po_created")
        .filter(Requisition.created_ts.isnot(None))
        .all()
    )
    cycle_hours = []
    for r in reqs:
        if r.created_ts:
            # po_ref set means status flipped; use last_event is not stored,
            # so we approximate with today - created (conservative upper bound).
            # Agents that set po_ref should ideally log a completion timestamp.
            age_h = (
                datetime.combine(today, datetime.min.time()) - r.created_ts.replace(tzinfo=None)
            ).total_seconds() / 3600
            cycle_hours.append(age_h)
    computed["reorder_cycle_days"] = (
        round(sum(cycle_hours) / len(cycle_hours) / 24, 2)
        if cycle_hours else None
    )
    computed["requisition_cycle_hours"] = (
        round(sum(cycle_hours) / len(cycle_hours), 2)
        if cycle_hours else None
    )

    # ── 12. Vendor OTIF: weighted avg of per-vendor otif_pct from scorecard mart
    vendor_rows = (
        db.query(VendorScorecard)
        .filter(VendorScorecard.period == period)
        .filter(VendorScorecard.entity == entity)
        .all()
    )
    otif_values = [v.otif_pct for v in vendor_rows if v.otif_pct is not None]
    computed["vendor_otif_pct"] = (
        round(sum(otif_values) / len(otif_values) * 100, 2)
        if otif_values else None
    )

    # ── 13. Dispatch to POD: avg actual transit days from C2 DispatchRecord
    dispatches = (
        db.query(DispatchRecord)
        .filter(
            DispatchRecord.entity == entity,
            DispatchRecord.status == "delivered",
            DispatchRecord.pod_ts.isnot(None),
            DispatchRecord.dispatch_ts.isnot(None),
        )
        .all()
    )
    transit_days = [
        (dr.pod_ts.date() - dr.dispatch_ts.date()).days
        for dr in dispatches
        if dr.pod_ts and dr.dispatch_ts
    ]
    computed["dispatch_to_pod_days"] = (
        round(sum(transit_days) / len(transit_days), 2) if transit_days else None
    )

    # ── 15. Time to fill: avg days on completed hire tasks
    hire_tasks = (
        db.query(Task)
        .filter(Task.task_type == "hire")
        .filter(Task.status == "SUCCESS")
        .all()
    )
    fill_days = []
    for t in hire_tasks:
        created = getattr(t, "created_at", None) or getattr(t, "created_ts", None)
        updated = getattr(t, "updated_at", None) or getattr(t, "updated_ts", None)
        if created and updated:
            fill_days.append((updated - created).days)
    computed["time_to_fill_days"] = (
        round(sum(fill_days) / len(fill_days), 2) if fill_days else None
    )

    # ── 16. AMC renewal rate: renewed contracts / expiring-within-90d per entity
    ninety_out = today + timedelta(days=90)
    expiring_contracts = (
        db.query(AmcContract)
        .filter(
            AmcContract.entity == entity,
            AmcContract.end_date >= today,
            AmcContract.end_date <= ninety_out,
        )
        .all()
    )
    renewed_contracts = sum(
        1 for c in expiring_contracts
        if c.status == "renewed" or c.renewal_ref is not None
    )
    computed["amc_renewal_rate_pct"] = (
        round(renewed_contracts / len(expiring_contracts) * 100, 2)
        if expiring_contracts else None
    )

    # ── 17. Revenue per FTE: total_open_receivables / headcount setting
    try:
        from app.core.config import settings as _cfg
        headcount = int(getattr(_cfg, "headcount", 0) or 0)
    except Exception:
        headcount = 0
    computed["revenue_per_fte"] = (
        round(total_open / headcount, 2) if headcount else None
    )

    # ── 18. Attrition risk score: mean H3 risk_score across active employees
    active_employees = (
        db.query(HrEmployee)
        .filter(HrEmployee.entity == entity, HrEmployee.status == "active")
        .all()
    )
    risk_scores = [e.risk_score for e in active_employees if e.risk_score is not None]
    computed["attrition_risk_score"] = (
        round(sum(risk_scores) / len(risk_scores), 2) if risk_scores else None
    )

    # ── 19. Talk-time %: outbound WA / total WA messages
    wa_out = (
        db.query(MessageLog)
        .filter(MessageLog.channel == "wa")
        .filter(MessageLog.direction == "out")
        .count()
    )
    wa_total = (
        db.query(MessageLog)
        .filter(MessageLog.channel == "wa")
        .count()
    )
    computed["talk_time_pct"] = (
        round(wa_out / wa_total * 100, 2) if wa_total else None
    )

    # ── 20. Pipeline coverage ratio: reserved for live CRM data
    computed["pipeline_coverage_ratio"] = None

    # ── 21. SCM claim resolution: avg days from raised_ts to settled_ts (C3)
    settled_claims = (
        db.query(ScmClaim)
        .filter(
            ScmClaim.entity == entity,
            ScmClaim.status == "settled",
            ScmClaim.settled_ts.isnot(None),
        )
        .all()
    )
    resolution_days = [
        (c.settled_ts.date() - c.raised_ts.date()).days
        for c in settled_claims
        if c.settled_ts and c.raised_ts
    ]
    computed["scm_claim_resolution_days"] = (
        round(sum(resolution_days) / len(resolution_days), 2)
        if resolution_days else None
    )

    # ── 22 & 23. Campaign delivery and response rates: avg across closed campaigns (K6)
    closed_campaigns = (
        db.query(Campaign)
        .filter(
            Campaign.entity == entity,
            Campaign.status == "closed",
            Campaign.kpi_json.isnot(None),
        )
        .all()
    )
    delivery_rates = [
        c.kpi_json["delivery_rate_pct"]
        for c in closed_campaigns
        if c.kpi_json and c.kpi_json.get("delivery_rate_pct") is not None
    ]
    response_rates = [
        c.kpi_json["response_rate_pct"]
        for c in closed_campaigns
        if c.kpi_json and c.kpi_json.get("response_rate_pct") is not None
    ]
    computed["campaign_delivery_rate_pct"] = (
        round(sum(delivery_rates) / len(delivery_rates), 2) if delivery_rates else None
    )
    computed["campaign_response_rate_pct"] = (
        round(sum(response_rates) / len(response_rates), 2) if response_rates else None
    )

    for kpi_id, value in computed.items():
        _kpi_set(db, kpi_id, value, period, entity=entity)

    db.commit()
    return {"kpis": computed, "period": str(period),
            "source_lane": KPI_SOURCE_LANE}
