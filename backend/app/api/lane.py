"""Lane API — inspect governance state, move the three switches.

GET   /lanes                  all lanes with their regime
POST  /lanes/{lane}/ensure    create a lane config with defaults
POST  /lanes/{lane}/autonomy  set A1 | A2 | A3
POST  /lanes/{lane}/kill-switch   pull or release
POST  /lanes/{lane}/dry-run       enable or disable
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.repository.lane_repository import LaneRepository


logger = logging.getLogger(__name__)

router = APIRouter(tags=["lanes"])


def _view(lane) -> dict:
    return {
        "lane": lane.lane,
        "mode": lane.mode,
        "autonomy_state": lane.autonomy_state,
        "kill_switch": lane.kill_switch,
        "dry_run": lane.dry_run,
        "daily_cap": lane.daily_cap,
        "quiet_start": (
            lane.quiet_start.isoformat() if lane.quiet_start else None
        ),
        "quiet_end": (
            lane.quiet_end.isoformat() if lane.quiet_end else None
        ),
        "notes": lane.notes,
        "updated_at": lane.updated_at,
    }


@router.get("/lanes")
def list_lanes(db: Session = Depends(get_db)):
    return [_view(l) for l in LaneRepository(db).list()]


class EnsureLaneRequest(BaseModel):
    mode: str = "supervised"
    daily_cap: int | None = None


@router.post("/lanes/{lane}/ensure")
def ensure_lane(
    lane: str,
    request: EnsureLaneRequest,
    db: Session = Depends(get_db),
):
    row = LaneRepository(db).ensure(
        lane, mode=request.mode, daily_cap=request.daily_cap
    )
    db.commit()
    return _view(row)


class AutonomyRequest(BaseModel):
    state: str


@router.post("/lanes/{lane}/autonomy")
def set_autonomy(
    lane: str,
    request: AutonomyRequest,
    db: Session = Depends(get_db),
):
    try:
        row = LaneRepository(db).set_autonomy(lane, request.state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return _view(row)


class KillSwitchRequest(BaseModel):
    pulled: bool


@router.post("/lanes/{lane}/kill-switch")
def set_kill_switch(
    lane: str,
    request: KillSwitchRequest,
    db: Session = Depends(get_db),
):
    try:
        row = LaneRepository(db).set_kill_switch(lane, request.pulled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    db.commit()
    return _view(row)


class DryRunRequest(BaseModel):
    enabled: bool


@router.post("/lanes/{lane}/dry-run")
def set_dry_run(
    lane: str,
    request: DryRunRequest,
    db: Session = Depends(get_db),
):
    try:
        row = LaneRepository(db).set_dry_run(lane, request.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    db.commit()
    return _view(row)
