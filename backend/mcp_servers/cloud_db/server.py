"""Cloud DB MCP Server — read-only analytics tools (WP-1).

The cloud store holds aggregates and marts only: KPI snapshots,
dealer/sku health, installed base. Agents get typed read tools with
fixed row limits; there is no write path here by design — the only
writer is the Local→Cloud shipper (app/sync/cloud_shipper.py).

Tools (MCP):
  - kpi.list(kpi_id?, period_from?, period_to?) -> rows (≤500)
  - kpi.latest(kpi_id, entity?) -> most recent snapshot
  - dealer.get(dealer_ref) -> dealer_mart row
  - sku.health(sku_ref?, branch?) -> sku_health rows (≤200)
  - installed_base.get(serial?) -> installed_base rows (≤200)

Every call raises CloudDatabaseNotConfigured if CLOUD_DATABASE_URL is
unset — callers surface a clean refusal, never fabricated data.
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_cloud_session_factory

logger = logging.getLogger(__name__)

MAX_ROWS = 500


def _session() -> Session:
    return get_cloud_session_factory()()


def _serialize(model_obj, columns: tuple[str, ...]) -> dict:
    out = {}
    for name in columns:
        val = getattr(model_obj, name)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        out[name] = val
    return out


# ── kpi tools ─────────────────────────────────────────────────────


def kpi_list(
    kpi_id: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> list[dict]:
    """KPI snapshots, newest first, capped at MAX_ROWS."""
    from app.models.enterprise import KpiSnapshot

    db = _session()
    try:
        q = (
            select(KpiSnapshot)
            .order_by(KpiSnapshot.period.desc())
            .limit(MAX_ROWS)
        )
        if kpi_id:
            q = q.where(KpiSnapshot.kpi_id == kpi_id)
        if period_from:
            q = q.where(KpiSnapshot.period >= date.fromisoformat(period_from))
        if period_to:
            q = q.where(KpiSnapshot.period <= date.fromisoformat(period_to))
        rows = list(db.execute(q).scalars())
        return [
            _serialize(r, (
                "kpi_id", "period", "entity", "branch", "owner_role",
                "value", "target", "baseline", "source_lane", "computed_ts",
            ))
            for r in rows
        ]
    finally:
        db.close()


def kpi_latest(kpi_id: str, entity: str | None = None) -> dict | None:
    """Most recent snapshot for one KPI (optionally per entity)."""
    from app.models.enterprise import KpiSnapshot

    db = _session()
    try:
        q = (
            select(KpiSnapshot)
            .where(KpiSnapshot.kpi_id == kpi_id)
            .order_by(KpiSnapshot.period.desc())
            .limit(1)
        )
        if entity:
            q = q.where(KpiSnapshot.entity == entity)
        row = db.execute(q).scalars().first()
        if row is None:
            return None
        return _serialize(row, (
            "kpi_id", "period", "entity", "branch", "owner_role",
            "value", "target", "baseline", "source_lane", "computed_ts",
        ))
    finally:
        db.close()


# ── dealer / sku / installed-base tools ───────────────────────────


def dealer_get(dealer_ref: str) -> dict | None:
    """One dealer_mart row."""
    from app.models.enterprise import DealerMart

    db = _session()
    try:
        row = db.get(DealerMart, dealer_ref)
        if row is None:
            return None
        return _serialize(row, (
            "dealer_ref", "branch", "outstanding_amt",
            "overdue_buckets_json", "payment_behavior_score",
            "last_order_ts", "offtake_13wk_json",
            "scheme_eligibility_json", "stock_opportunity_json",
            "refreshed_ts",
        ))
    finally:
        db.close()


def sku_health(
    sku_ref: str | None = None,
    branch: str | None = None,
) -> list[dict]:
    """sku_health rows, optionally filtered, capped at 200."""
    from app.models.enterprise import SkuHealth

    db = _session()
    try:
        q = select(SkuHealth).limit(200)
        if sku_ref:
            q = q.where(SkuHealth.sku_ref == sku_ref)
        if branch:
            q = q.where(SkuHealth.branch == branch)
        rows = list(db.execute(q).scalars())
        return [
            _serialize(r, (
                "sku_ref", "branch", "period", "on_hand", "weeks_cover",
                "ageing_buckets_json", "eol_flag", "action_reco",
            ))
            for r in rows
        ]
    finally:
        db.close()


def installed_base(serial: str | None = None) -> list[dict]:
    """installed_base rows, optionally filtered by serial, capped at 200."""
    from app.models.enterprise import InstalledBase

    db = _session()
    try:
        q = select(InstalledBase).limit(200)
        if serial:
            q = q.where(InstalledBase.serial == serial)
        rows = list(db.execute(q).scalars())
        return [
            _serialize(r, (
                "asset_id", "serial", "customer_ref", "site_text",
                "product_ref", "sold_invoice_ref", "install_ts",
                "warranty_end", "amc_contract_ref", "amc_end", "status",
                "last_service_ts",
            ))
            for r in rows
        ]
    finally:
        db.close()


def list_tools() -> list[dict]:
    """MCP tool catalogue — what Skill authors may call."""
    return [
        {"name": "kpi.list", "write": False,
         "desc": "KPI snapshots with optional kpi_id/period filters"},
        {"name": "kpi.latest", "write": False,
         "desc": "Most recent snapshot for one KPI"},
        {"name": "dealer.get", "write": False,
         "desc": "One dealer_mart row by dealer_ref"},
        {"name": "sku.health", "write": False,
         "desc": "sku_health rows filtered by sku_ref/branch"},
        {"name": "installed_base.get", "write": False,
         "desc": "installed_base rows filtered by serial"},
    ]
