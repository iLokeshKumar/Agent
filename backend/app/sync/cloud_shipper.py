"""Local→Cloud nightly shipper — one-way aggregate push (WP-1).

Local store (`yexis`) is the operational source of truth. The cloud
store (`yexis_agent_cloud`) holds the analytics mirrors: kpi_snapshot,
dealer_mart, sku_health, agent_kpi_event, installed_base.

Rules from the tech spec:
  - One-way only: the shipper writes to cloud, never reads back.
  - Idempotent: re-running a night re-upserts the same rows, no dupes.
  - Watermarked: `sync_watermark` (lane=CLOUD_SHIP, source=local_to_cloud)
    records the last completed run so ops can see freshness.

The cloud side is discovered lazily: if CLOUD_DATABASE_URL is unset the
shipper reports `skipped` instead of failing — DEV keeps working with
no cloud store, mirroring how Zoho unconfigured skips the F3 pull.

Both engines use the same SQLAlchemy models for now (the models live in
the Local metadata); when the cloud store is physically split, only
the engine wiring here changes.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.db import (
    CloudDatabaseNotConfigured,
    get_cloud_session_factory,
)
from app.models.sync_watermark import SyncWatermark

logger = logging.getLogger(__name__)

SHIP_LANE = "CLOUD_SHIP"
SHIP_SOURCE = "local_to_cloud"

# Local table → cloud table mirror map (same model class today).
MIRROR_TABLES = (
    "kpi_snapshot",
    "dealer_mart",
    "sku_health",
    "installed_base",
)


def _mirror_rows(local_db: Session, table: str) -> list[dict]:
    """Read all rows of one mirror table as plain dicts.

    Table names come from the fixed MIRROR_TABLES tuple — never from
    user input — so the getattr on the models module is safe.
    """
    from app.models import enterprise as ent

    model = getattr(ent, {
        "kpi_snapshot": "KpiSnapshot",
        "dealer_mart": "DealerMart",
        "sku_health": "SkuHealth",
        "installed_base": "InstalledBase",
    }[table])

    rows = local_db.query(model).all()
    out = []
    for r in rows:
        d = {}
        for col in model.__table__.columns:
            val = getattr(r, col.name)
            d[col.name] = val
        out.append(d)
    return out


def ship_local_to_cloud(local_db: Session) -> dict:
    """Push every mirror table Local→Cloud. Returns a per-table report."""
    try:
        cloud_factory = get_cloud_session_factory()
    except CloudDatabaseNotConfigured as exc:
        logger.warning("Cloud shipper skipped: %s", exc)
        return {"status": "skipped", "reason": str(exc)}

    results: dict[str, dict] = {}
    cloud = cloud_factory()
    try:
        from app.models.agent import Base

        # Ensure mirror tables exist on the cloud side (idempotent DDL).
        # The session's bind IS the cloud engine — works for the real
        # Postgres session and the SQLite test double alike.
        from app.models import enterprise as ent

        cloud_tables = [getattr(ent, n).__table__ for n in (
            "KpiSnapshot", "DealerMart", "SkuHealth", "InstalledBase",
        )]
        Base.metadata.create_all(bind=cloud.get_bind(), tables=cloud_tables)

        for table in MIRROR_TABLES:
            rows = _mirror_rows(local_db, table)
            pushed = 0
            for row in rows:
                _upsert_row(cloud, table, row)
                pushed += 1
            results[table] = {"status": "ok", "rows": pushed}

        cloud.commit()

        _stamp_watermark(local_db, results)
        results["_watermark"] = {
            "status": "ok",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("Cloud shipper completed: %s", {
            k: v.get("rows") for k, v in results.items() if k != "_watermark"
        })
        return {"status": "completed", "results": results}
    except Exception as exc:
        cloud.rollback()
        logger.exception("Cloud shipper failed")
        return {"status": "error", "error": str(exc)}
    finally:
        cloud.close()


def _upsert_row(cloud: Session, table: str, row: dict) -> None:
    """Merge one row into the cloud table by its primary key."""
    from app.models import enterprise as ent

    model = getattr(ent, {
        "kpi_snapshot": "KpiSnapshot",
        "dealer_mart": "DealerMart",
        "sku_health": "SkuHealth",
        "installed_base": "InstalledBase",
    }[table])
    cloud.merge(model(**row))


def _stamp_watermark(local_db: Session, results: dict) -> None:
    """Record ship completion in the local sync_watermark register."""
    wm = (
        local_db.query(SyncWatermark)
        .filter(
            SyncWatermark.lane == SHIP_LANE,
            SyncWatermark.source == SHIP_SOURCE,
        )
        .first()
    )
    now = datetime.now(timezone.utc).isoformat()
    if wm is None:
        wm = SyncWatermark(lane=SHIP_LANE, source=SHIP_SOURCE)
        local_db.add(wm)
    wm.cursor = now
    wm.extra = {
        k: v.get("rows", 0)
        for k, v in results.items()
        if k != "_watermark"
    }
    local_db.commit()


def get_ship_status(local_db: Session) -> dict:
    """Freshness of the last completed ship, for /sync/status."""
    wm = (
        local_db.query(SyncWatermark)
        .filter(
            SyncWatermark.lane == SHIP_LANE,
            SyncWatermark.source == SHIP_SOURCE,
        )
        .first()
    )
    if wm is None:
        return {"last_ship": None, "rows_last_run": {}}
    return {
        "last_ship": wm.cursor,
        "rows_last_run": wm.extra or {},
    }
