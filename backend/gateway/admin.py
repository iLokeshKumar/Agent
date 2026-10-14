"""Gateway admin endpoints — ops dashboard, kill-switch shortcuts, DLQ.

These are low-traffic operational endpoints:
  GET  /gateway/ops         queue depths, lane states, DLQ summary
  GET  /gateway/dlq         full DLQ item list
  POST /gateway/dlq/drain   force-retry all DLQ items immediately
  POST /gateway/dlq/clear   discard all DLQ items (use when items are stale)
"""

import logging

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.action_ledger import ActionLedger
from app.models.gateway_event import GatewayEvent
from app.repository.lane_repository import LaneRepository
from gateway.dlq import dlq

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gateway-admin"])

# Injected by main.py once the scheduler lanes are known.
_scheduler_lanes: list = []
_backend_url: str = "http://127.0.0.1:8000"


def configure(lanes: list, backend_url: str) -> None:
    global _scheduler_lanes, _backend_url
    _scheduler_lanes = lanes
    _backend_url = backend_url


@router.get("/gateway/ops")
def ops_dashboard(db: Session = Depends(get_db)):
    """Ops dashboard: lane states, today's submission counts, DLQ summary.

    'submissions_today' counts successful agent actions from the ledger
    (a quick proxy for queue throughput without a dedicated counter table).
    'recent_failures' shows the last 10 rejected gateway events.
    """
    lane_repo = LaneRepository(db)
    lane_rows = lane_repo.list()

    lanes_view = []
    for row in lane_rows:
        count = lane_repo.today_execution_count(row.lane)
        lanes_view.append({
            "lane": row.lane,
            "kill_switch": row.kill_switch,
            "autonomy_state": row.autonomy_state,
            "daily_cap": row.daily_cap,
            "submissions_today": count,
            "cap_exceeded": lane_repo.cap_exceeded(row),
            "quiet_start": row.quiet_start.isoformat() if row.quiet_start else None,
            "quiet_end": row.quiet_end.isoformat() if row.quiet_end else None,
        })

    # Scheduled lane next-due info (in-process state — not DB-backed).
    scheduler_view = []
    import time as _time
    _now = _time.time()
    for lane in _scheduler_lanes:
        last_ran_ts = lane._last_run or None
        if lane.every_seconds and last_ran_ts:
            next_due_secs = max(0, int(lane.every_seconds - (_now - last_ran_ts)))
            next_due_ts = last_ran_ts + lane.every_seconds
        else:
            next_due_secs = None
            next_due_ts = None
        from datetime import datetime as _dt, timezone as _tz
        scheduler_view.append({
            "name": lane.name,
            "task_type": lane.task_type,
            "every_seconds": lane.every_seconds,
            "last_ran_iso": _dt.fromtimestamp(last_ran_ts, tz=_tz.utc).isoformat() if last_ran_ts else None,
            "next_due_secs": next_due_secs,
            "next_due_iso": _dt.fromtimestamp(next_due_ts, tz=_tz.utc).isoformat() if next_due_ts else None,
            "in_dlq": any(i.lane_name == lane.name for i in dlq._items.values()),
        })

    # Last 10 rejected webhook events.
    recent_failures = db.execute(
        select(GatewayEvent)
        .where(GatewayEvent.status == "rejected")
        .order_by(GatewayEvent.created_at.desc())
        .limit(10)
    ).scalars().all()

    failures_view = [
        {
            "source": e.source,
            "event_id": e.event_id,
            "note": e.note,
            "created_at": e.created_at.isoformat(),
        }
        for e in recent_failures
    ]

    return {
        "lanes": lanes_view,
        "scheduler": scheduler_view,
        "dlq_depth": dlq.depth(),
        "recent_failures": failures_view,
    }


@router.get("/gateway/dlq")
def list_dlq():
    """List all items currently in the dead letter queue."""
    return {"items": dlq.status(), "depth": dlq.depth()}


@router.post("/gateway/dlq/drain")
def drain_dlq():
    """Force-retry all DLQ items immediately, ignoring backoff."""
    with httpx.Client() as client:
        count = dlq.drain(client)
    return {"drained": count, "remaining": dlq.depth()}


@router.post("/gateway/dlq/clear")
def clear_dlq():
    """Discard all DLQ items without retrying (stale items, post-incident)."""
    depth = dlq.depth()
    dlq._items.clear()
    logger.warning("DLQ cleared by operator; %d items discarded", depth)
    return {"cleared": depth}
