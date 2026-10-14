"""Sync API — status, manual trigger, cache access."""

import logging

from fastapi import APIRouter, HTTPException

from app.sync.scheduler import get_sync_scheduler
from app.sync.cache import SyncCache
from app.sync.cloud_shipper import get_ship_status
from app.sync.nightly import run_nightly


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sync",
    tags=["sync"],
)


@router.get("/status")
def sync_status():
    """Show current sync status, last run, cache freshness."""
    scheduler = get_sync_scheduler()
    syncer = scheduler.syncer

    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        ship = get_ship_status(db)
    finally:
        db.close()

    return {
        "scheduler": {
            "enabled": scheduler.enabled,
            "running": scheduler.running,
            "interval_seconds": scheduler.interval,
            "last_run": scheduler.last_run,
        },
        "cloud_shipper": ship,
        "sync": syncer.get_sync_status(),
    }


@router.post("/nightly")
def nightly_run():
    """Manual run of the nightly analytics chain.

    Same code path the Gateway `nightly_analytics` lane triggers:
    marts → KPIs → Local→Cloud ship → DPDP purge. Never raises;
    per-stage results (and failures) come back in the report.
    """
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        return run_nightly(db)
    finally:
        db.close()


@router.post("/trigger")
def sync_trigger():
    """Trigger an immediate full sync."""
    scheduler = get_sync_scheduler()
    result = scheduler.trigger_now()

    return result


@router.post("/trigger/{target}")
def sync_trigger_target(target: str):
    """Trigger sync for a single target."""
    valid_targets = {
        "invoices", "bills", "contacts",
        "journals", "chart_of_accounts",
    }

    if target not in valid_targets:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target. Valid: {', '.join(sorted(valid_targets))}",
        )

    scheduler = get_sync_scheduler()
    result = scheduler.syncer.sync_target(target)

    return {
        "target": target,
        "result": result,
    }


@router.get("/cache/{key}")
def cache_get(key: str):
    """Get cached data by key."""
    cache = SyncCache()
    cached = cache.get(key)

    if cached is None:
        raise HTTPException(
            status_code=404,
            detail=f"Cache key '{key}' not found",
        )

    # Return metadata + first 100 records
    return {
        "key": key,
        "last_synced": cached.get("last_synced"),
        "count": cached.get("count", 0),
        "preview": cached.get("data", [])[:100],
    }


@router.get("/cache")
def cache_list():
    """List all cached data keys with metadata."""
    cache = SyncCache()

    return {
        "keys": cache.list_keys(),
        "metadata": cache.get_all_metadata(),
    }


@router.delete("/cache/{key}")
def cache_clear(key: str):
    """Clear a specific cache key."""
    cache = SyncCache()
    cache.clear(key)

    return {"status": "cleared", "key": key}


@router.delete("/cache")
def cache_clear_all():
    """Clear all cached data."""
    cache = SyncCache()
    cache.clear()

    return {"status": "all cache cleared"}
