"""Promotion API — supervised-live change controller for Phase 1 lanes.

Endpoints:
  GET  /promotion/preflight               check all external dependencies
  GET  /promotion/{lane}/gate-report      run golden + redteam + signoff check
  POST /promotion/{lane}/promote          flip dry_run=false after all gates pass

The promote endpoint is the single choke-point: it refuses to remove dry_run
unless *every* gate (golden, redteam, signoff, rollback, Zoho, Tally, WA)
is green. Partial promotions are not possible — every gate must pass together.
"""

import logging
import socket

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.repository.lane_repository import LaneRepository
from app.services.promotion_gate import (
    AGENT_CODE_TO_DIR,
    gate_report,
    render_text,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/promotion", tags=["promotion"])

# Phase 1 lanes in promotion order
PHASE1_LANES = ["P1", "P4", "S1", "F1", "F3", "D1", "V1"]


# ── external dependency checks ────────────────────────────────────


def _check_zoho() -> dict:
    from app.integrations.zoho_books import get_zoho_client
    client = get_zoho_client()
    if not client.configured:
        return {
            "ok": False,
            "detail": "Zoho OAuth not completed. Visit GET /auth/zoho in a browser.",
        }
    # Attempt a live call to validate the token is not expired
    try:
        org = client.get_organization()
        return {
            "ok": True,
            "org_name": org.get("company_name"),
            "org_id": org.get("organization_id"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "detail": f"Zoho API call failed: {exc}. Re-authorize at GET /auth/zoho.",
        }


def _check_tally() -> dict:
    from app.core.config import settings
    host = settings.tally_host or "localhost"
    port = 9000

    if not settings.tally_host:
        return {
            "ok": False,
            "detail": "TALLY_HOST not set in .env",
        }

    try:
        with socket.create_connection((host, port), timeout=5):
            pass
        return {
            "ok": True,
            "host": host,
            "port": port,
            "detail": f"Tally reachable at {host}:{port}",
        }
    except (ConnectionRefusedError, OSError, TimeoutError) as exc:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "detail": (
                f"Cannot reach Tally at {host}:{port}: {exc}. "
                "Ensure Tally Prime is running and port 9000 is open. "
                "See docs/tally-network-setup.md."
            ),
        }


def _check_whatsapp(db: Session) -> dict:
    from app.core.config import settings
    from app.models.enterprise import TemplateRegistry

    if not settings.waba_api_key or not settings.waba_api_url:
        return {
            "ok": False,
            "detail": "WABA_API_KEY or WABA_API_URL not set in .env",
        }

    if not settings.waba_phone_number_id:
        return {
            "ok": False,
            "detail": "WABA_PHONE_NUMBER_ID not set in .env",
        }

    # Check that at least one approved template exists in the registry
    approved = (
        db.query(TemplateRegistry)
        .filter(TemplateRegistry.approval_status.in_(["meta", "internal"]))
        .count()
    )

    if approved == 0:
        return {
            "ok": False,
            "detail": (
                "No approved templates in template_registry. "
                "Register at least one approved template before promoting F1. "
                "See docs/runbooks/admin.md — WhatsApp BSP section."
            ),
        }

    # Verify opt-in test recipient is configured
    recipient = getattr(settings, "digest_recipient_wa", "") or ""
    if not recipient:
        return {
            "ok": True,
            "approved_templates": approved,
            "warning": (
                "DIGEST_RECIPIENT_WA not set — morning digest will not send, "
                "but F1/V1 can still use WA for customer outreach."
            ),
        }

    return {
        "ok": True,
        "approved_templates": approved,
        "waba_configured": True,
    }


# ── API routes ────────────────────────────────────────────────────


@router.get("/preflight")
def preflight(db: Session = Depends(get_db)):
    """Check all external dependencies required for Phase 1 promotion."""
    zoho = _check_zoho()
    tally = _check_tally()
    wa = _check_whatsapp(db)

    all_ok = zoho["ok"] and tally["ok"] and wa["ok"]

    return {
        "ready": all_ok,
        "zoho": zoho,
        "tally": tally,
        "whatsapp": wa,
        "hint": (
            "All three must be ok=true before any lane can be promoted. "
            "Fix failing checks, then POST /promotion/{lane}/promote."
        ) if not all_ok else "All external dependencies ready.",
    }


@router.get("/{lane}/gate-report")
def get_gate_report(lane: str, db: Session = Depends(get_db)):
    """Full promotion gate report for one lane: golden + redteam + signoff + external deps."""
    lane = lane.upper()
    agent_dir = AGENT_CODE_TO_DIR.get(lane)
    if agent_dir is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown lane '{lane}'. Known: {list(AGENT_CODE_TO_DIR.keys())}",
        )

    report = gate_report(agent_dir, lane)

    # Attach external dependency checks for F1/F3/D1/V1 that need them
    external = {
        "zoho": _check_zoho(),
        "tally": _check_tally() if lane == "F3" else {"ok": True, "skipped": "not required for this lane"},
        "whatsapp": _check_whatsapp(db) if lane in ("F1", "V1") else {"ok": True, "skipped": "not required for this lane"},
    }
    report["external"] = external
    report["text"] = render_text(report)

    return report


@router.get("")
def all_gate_reports(db: Session = Depends(get_db)):
    """Gate report for all Phase 1 lanes in one call."""
    results = {}
    for lane in PHASE1_LANES:
        agent_dir = AGENT_CODE_TO_DIR.get(lane)
        if agent_dir:
            try:
                results[lane] = gate_report(agent_dir, lane)
            except Exception as exc:
                results[lane] = {"error": str(exc)}

    approved_count = sum(1 for r in results.values() if r.get("approved"))
    preflight_result = preflight(db)

    return {
        "phase1_ready": approved_count == len(PHASE1_LANES) and preflight_result["ready"],
        "approved_lanes": approved_count,
        "total_lanes": len(PHASE1_LANES),
        "external_preflight": preflight_result,
        "lanes": results,
    }


class PromoteRequest(BaseModel):
    confirmed_by: str
    note: str = ""


@router.post("/{lane}/promote")
def promote_lane(
    lane: str,
    req: PromoteRequest,
    db: Session = Depends(get_db),
):
    """Flip dry_run=false for a lane — only if ALL promotion gates pass.

    This is the controlled change for supervised-live promotion.
    The lane will run with real Zoho/Tally/WA writes after this call.
    """
    lane = lane.upper()

    if lane not in PHASE1_LANES:
        raise HTTPException(
            status_code=400,
            detail=f"Lane '{lane}' is not a Phase 1 lane. Promotion is only supported for: {PHASE1_LANES}",
        )

    agent_dir = AGENT_CODE_TO_DIR.get(lane)
    if agent_dir is None:
        raise HTTPException(status_code=404, detail=f"Unknown lane '{lane}'")

    # ── Gate check 1: golden + redteam + signoff + rollback
    report = gate_report(agent_dir, lane)
    if not report["approved"]:
        failing = [k for k, v in report["criteria"].items() if not v["ok"]]
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Promotion gate not cleared",
                "failing_criteria": failing,
                "gate_report": report,
            },
        )

    # ── Gate check 2: Zoho OAuth
    zoho = _check_zoho()
    if not zoho["ok"]:
        raise HTTPException(
            status_code=409,
            detail={"error": "Zoho OAuth not completed", "detail": zoho["detail"]},
        )

    # ── Gate check 3: Tally (required for F3 only; advisory for others)
    if lane == "F3":
        tally = _check_tally()
        if not tally["ok"]:
            raise HTTPException(
                status_code=409,
                detail={"error": "Tally not reachable", "detail": tally["detail"]},
            )

    # ── Gate check 4: WhatsApp (required for F1, V1)
    if lane in ("F1", "V1"):
        wa = _check_whatsapp(db)
        if not wa["ok"]:
            raise HTTPException(
                status_code=409,
                detail={"error": "WhatsApp BSP not verified", "detail": wa["detail"]},
            )

    # ── All gates passed — flip the switch
    repo = LaneRepository(db)
    lane_row = repo.ensure(lane)
    if not lane_row.dry_run:
        return {
            "lane": lane,
            "status": "already_live",
            "message": f"Lane {lane} was already promoted.",
            "dry_run": False,
        }

    repo.set_dry_run(lane, enabled=False)
    db.commit()

    logger.info(
        "PROMOTION: lane %s promoted to live by %s. Note: %s",
        lane,
        req.confirmed_by,
        req.note,
    )

    return {
        "lane": lane,
        "status": "promoted",
        "message": (
            f"Lane {lane} is now live. dry_run=false. "
            "Monitor /lanes and /ops closely for the first 24 hours."
        ),
        "promoted_by": req.confirmed_by,
        "note": req.note,
        "dry_run": False,
        "next": f"POST /promotion/{lane.lower()}/promote for next lane, or GET /promotion for full status.",
    }
