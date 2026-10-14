"""Cloud-DB MCP router — read-only typed tools over the analytics store.

Mirrors the mcp_local_db pattern: FastAPI routes = MCP tool surface.
Every route resolves the cloud session lazily; an unset
CLOUD_DATABASE_URL yields HTTP 503 (clean refusal, no fabricated data),
not a 500.

No write route exists in this module by construction — the cloud store
is written only by the Local→Cloud shipper.
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.core.db import CloudDatabaseNotConfigured
from mcp_servers.cloud_db import server as cloud_server

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp/cloud_db", tags=["mcp-cloud-db"])


def _guard(fn, *args, **kwargs):
    """Run a cloud tool, mapping unconfigured → 503."""
    try:
        return fn(*args, **kwargs)
    except CloudDatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/kpi")
def kpi_list(
    kpi_id: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
):
    return _guard(
        cloud_server.kpi_list,
        kpi_id=kpi_id,
        period_from=period_from,
        period_to=period_to,
    )


@router.get("/kpi/latest")
def kpi_latest(
    kpi_id: str = Query(...),
    entity: str | None = None,
):
    row = _guard(cloud_server.kpi_latest, kpi_id=kpi_id, entity=entity)
    if row is None:
        raise HTTPException(status_code=404, detail=f"KPI '{kpi_id}' not found")
    return row


@router.get("/dealer/{dealer_ref}")
def dealer_get(dealer_ref: str):
    row = _guard(cloud_server.dealer_get, dealer_ref)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Dealer '{dealer_ref}' not found"
        )
    return row


@router.get("/sku")
def sku_health(
    sku_ref: str | None = None,
    branch: str | None = None,
):
    return _guard(cloud_server.sku_health, sku_ref=sku_ref, branch=branch)


@router.get("/installed_base")
def installed_base(serial: str | None = None):
    return _guard(cloud_server.installed_base, serial=serial)


@router.get("/tools")
def tools():
    """MCP tool catalogue for Skill authors."""
    return {"tools": cloud_server.list_tools()}


# Explicit proof for the contract test: this router defines only GETs.
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@router.get("/_write_surface")
def write_surface():
    """Introspection endpoint: the set of write methods exposed here."""
    exposed: set[str] = set()
    for route in router.routes:
        exposed |= (route.methods or set()) - {"HEAD", "OPTIONS"}
    return {
        "write_methods": sorted(exposed & _WRITE_METHODS),
        "read_only": not (exposed & _WRITE_METHODS),
    }
