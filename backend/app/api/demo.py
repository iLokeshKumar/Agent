"""Run showcase scenarios against connected providers and current records."""
import logging
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.repository.task_repository import TaskRepository
from app.repository.approval_repository import ApprovalRepository
from orchestrator.queue import task_queue

router = APIRouter(prefix="/demo", tags=["demo"])
logger = logging.getLogger(__name__)

SCENARIOS = [
    {
        "id": "f1_collections",
        "name": "F1 — Collections Cadence",
        "subtitle": "AR Aging & Dunning",
        "description": "Pulls overdue invoices from Zoho Books, segments by aging bucket (0–30, 31–60, 61–90, 90+ days), and drafts WhatsApp reminders per customer.",
        "agent_code": "F1",
        "task_type": "collections",
        "icon": "₹",
        "color": "#f59e0b",
        "audience": "Finance / Branch Head",
    },
    {
        "id": "f3_books_tally",
        "name": "F3 — Books→Tally Sync",
        "subtitle": "Daily Journal Staging",
        "description": "Fetches this month's Zoho Books journals, maps accounts to Tally ledger names, and stages vouchers for accountant approval before anything posts.",
        "agent_code": "F3",
        "task_type": "books_to_tally",
        "icon": "⟷",
        "color": "#8b5cf6",
        "audience": "Finance / Accountant",
    },
    {
        "id": "m2_gsm_brief",
        "name": "M2 — GSM Morning Brief",
        "subtitle": "Territory Command Centre",
        "description": "Aggregates entity-wide dealer portfolio, outstanding receivables, and executive KPI dashboard for the Senior Sales Manager.",
        "agent_code": "M2",
        "task_type": "gsm_command",
        "icon": "⌘",
        "color": "#10b981",
        "audience": "GSM / Leadership",
    },
    {
        "id": "p1_purchase",
        "name": "P1 — Purchase Request",
        "subtitle": "Intake to PO",
        "description": "Requires a real purchase request; fixed example orders are not supported.",
        "agent_code": "P1",
        "task_type": "purchase_request",
        "icon": "⧫",
        "color": "#3b82f6",
        "audience": "Purchase / Operations",
        "run_enabled": False,
        "run_label": "Real request required",
    },
]

# ── Zoho data helpers ──────────────────────────────────────────────────────────

def _zoho_invoices_for_f1() -> tuple[list[dict], dict]:
    """Pull real overdue Zoho Books invoices for the F1 scenario."""
    from app.integrations.zoho_books import get_zoho_client, ZohoBooksError
    client = get_zoho_client()

    if not client.configured:
        raise HTTPException(
            status_code=503,
            detail="Zoho Books not configured. Visit /auth/zoho first.",
        )

    try:
        invoices = client.list_invoices(status="overdue", per_page=25)
    except ZohoBooksError as exc:
        logger.exception("Zoho Books error fetching overdue invoices")
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch overdue invoices from Zoho Books: {exc}",
        ) from exc

    f1_invoices = []

    for inv in invoices[:20]:
        balance = float(inv.get("balance") or inv.get("outstanding_amount") or 0)
        due_date = inv.get("due_date") or inv.get("payment_expected_date")
        if balance <= 0 or not due_date:
            continue
        invoice_id = str(inv.get("invoice_id") or "")
        f1_invoices.append({
            "invoice_number": inv.get("invoice_number") or invoice_id,
            "customer": inv.get("customer_name") or inv.get("contact_name"),
            "outstanding_amount": balance,
            "due_date": due_date,
            "contact_email": inv.get("email") or "",
            "contact_phone": inv.get("mobile") or inv.get("phone") or "",
        })

    zoho_snapshot = {
        "source": "Zoho Books",
        "data_mode": "live",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "invoice_count": len(f1_invoices),
        "total_outstanding": round(sum(i["outstanding_amount"] for i in f1_invoices), 2),
        "customers": list({i["customer"] for i in f1_invoices})[:8],
        "note": "No overdue invoices with a usable due date and outstanding balance were returned."
        if not f1_invoices else "",
    }

    payload = {
        "invoices": f1_invoices,
        "follow_up_action": "REMINDER",
        "source": "demo",
    }
    return payload, zoho_snapshot


# ── Scenario runner ────────────────────────────────────────────────────────────

@router.get("/scenarios")
def get_scenarios():
    return SCENARIOS


@router.get("/preflight")
def preflight(request: Request):
    from app.core.auth import get_current_user
    from app.integrations.zoho_books import get_zoho_client, zoho_user_context
    actor = get_current_user(request)
    with zoho_user_context(actor.username if actor else None):
        client = get_zoho_client()
        configured = client.configured
        organization_id = client.organization_id
    return {
        "zoho_configured": configured,
        "zoho_org_id": organization_id,
        "zoho_connection": (
            "personal" if actor and configured
            else "shared_env" if configured
            else "not_configured"
        ),
        "zoho_data_center": client.data_center,
        "hint": (
            "Ready" if configured else
            "Connect Zoho in Integrations or configure the shared .env connection."
        ),
    }


@router.post("/run/{scenario_id}")
def run_scenario(scenario_id: str, request: Request, db: Session = Depends(get_db)):
    """Fire a demo scenario. Returns task_id + Zoho data snapshot."""
    from app.core.auth import get_current_user
    actor = get_current_user(request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in to run scenarios.")
    if not actor.can_run_demo(scenario_id):
        raise HTTPException(status_code=403, detail="You do not have permission to run this scenario.")

    return _run_scenario(
        scenario_id,
        db,
        requested_by=actor.username,
    )


def _run_scenario(
    scenario_id: str,
    db: Session,
    requested_by: str,
) -> dict:
    """Create and queue a demo scenario under its actual requesting user."""
    from app.integrations.zoho_books import zoho_user_context
    with zoho_user_context(requested_by):
        return _run_scenario_for_user(scenario_id, db, requested_by)


def _run_scenario_for_user(
    scenario_id: str,
    db: Session,
    requested_by: str,
) -> dict:
    """Prepare a scenario while using the requesting user's provider connections."""
    scenario_map = {s["id"]: s for s in SCENARIOS}
    if scenario_id not in scenario_map:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario_id}")
    if not scenario_map[scenario_id].get("run_enabled", True):
        raise HTTPException(
            status_code=409,
            detail=(
                "This scenario requires a real purchase request and cannot use fixed example data."
            ),
        )

    task_repo = TaskRepository(db)
    zoho_snapshot: dict | None = None
    extra: dict = {}

    if scenario_id == "f1_collections":
        payload, zoho_snapshot = _zoho_invoices_for_f1()
        task = task_repo.create(
            task_type="collections",
            source="demo",
            priority=5,
            requested_by=requested_by,
            payload=payload,
        )

    elif scenario_id == "f3_books_tally":
        today = date.today()

        from app.integrations.zoho_books import get_zoho_client
        client = get_zoho_client()
        if not client.configured:
            raise HTTPException(
                status_code=503,
                detail="Zoho Books is not configured. Connect Zoho before running this scenario.",
            )

        payload = {
            "start_date": str(today.replace(day=1)),
            "end_date": str(today),
            "entity": "YEXIS-EL",
            "company": "YEXIS ELECTRONICS - K",
            "source": "demo",
        }

        task = task_repo.create(
            task_type="books_to_tally",
            source="demo",
            priority=5,
            requested_by=requested_by,
            payload=payload,
        )
        zoho_snapshot = {
            "source": "Zoho Books",
            "data_mode": "live",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "date_range": f"{today.replace(day=1)} → {today}",
            "note": "The task fetches current journal entries from Zoho Books when it runs.",
        }

    elif scenario_id == "m2_gsm_brief":
        from app.models.enterprise import DealerMart, KpiSnapshot
        from sqlalchemy import or_
        dealer_count = db.query(DealerMart).filter(
            DealerMart.dealer_ref.not_like("DEMO-%")
        ).count()
        kpi_count = db.query(KpiSnapshot).filter(
            or_(KpiSnapshot.source_lane.is_(None), KpiSnapshot.source_lane != "demo")
        ).count()

        territory_task = task_repo.create(
            task_type="gsm_command",
            source="demo",
            priority=5,
            requested_by=requested_by,
            payload={"action": "territory", "entity": "YEXIS-EL"},
        )
        topline_task = task_repo.create(
            task_type="gsm_command",
            source="demo",
            priority=5,
            requested_by=requested_by,
            payload={"action": "topline", "entity": "YEXIS-EL"},
        )
        task_queue.put(task_id=topline_task.id, priority=5)
        extra["secondary_task_id"] = str(topline_task.id)
        task = territory_task
        zoho_snapshot = {
            "source": "DealerMart + KpiSnapshot (local mart)",
            "data_mode": "live",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "dealers": dealer_count,
            "kpi_rows": kpi_count,
            "note": (
                "No synced dealer or KPI data is available yet."
                if dealer_count == 0 and kpi_count == 0 else
                "Reads current records from the local analytics mart."
            ),
        }

    else:
        raise HTTPException(status_code=400, detail="Unhandled scenario")

    task_queue.put(task_id=task.id, priority=5)
    db.commit()

    return {
        "scenario_id": scenario_id,
        "task_id": str(task.id),
        "correlation_id": str(task.correlation_id),
        "zoho_snapshot": zoho_snapshot,
        "started_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


@router.get("/task/{task_id}")
def poll_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """Poll a demo task — returns status, formatted output, ledger, and any pending approval."""
    task_repo = TaskRepository(db)
    task = task_repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    from app.models.action_ledger import ActionLedger
    ledger = (
        db.query(ActionLedger)
        .filter(ActionLedger.task_id == task_id)
        .order_by(ActionLedger.created_at)
        .all()
    )

    approval_info = None
    if task.status == "AWAITING_APPROVAL":
        approval_repo = ApprovalRepository(db)
        pending = approval_repo.get_pending_by_task(task.id)
        if pending:
            approval_info = {
                "approval_id": str(pending.id),
                "approval_type": pending.approval_type,
                "payload": pending.payload,
            }

    return {
        "task_id": str(task.id),
        "task_type": task.task_type,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "ledger": [
            {
                "action": e.action,
                "status": e.status,
                "tool": e.tool_name,
                "approval_status": e.approval_status,
                "ts": e.created_at.isoformat() if e.created_at else None,
            }
            for e in ledger
        ],
        "approval": approval_info,
    }


@router.post("/approve/{approval_id}")
def approve_demo(
    approval_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    """Approve a HITL gate — for demo flow. Re-queues the task."""
    from app.core.auth import get_current_user
    actor = get_current_user(request)
    if not actor:
        raise HTTPException(status_code=401, detail="Sign in to approve tasks.")
    if not actor.can("approvals", "edit"):
        raise HTTPException(status_code=403, detail="You do not have permission to approve tasks.")
    approval_repo = ApprovalRepository(db)
    approval = approval_repo.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Approval is {approval.status}, not PENDING")

    approval_repo.approve(approval, approved_by=actor.username, reason="Demo approval")

    task_repo = TaskRepository(db)
    task = task_repo.get(approval.task_id)
    if task and task.status == "AWAITING_APPROVAL":
        task.status = "QUEUED"
        db.commit()
        task_queue.put(task_id=task.id, priority=5)

    return {"approved": True, "task_id": str(approval.task_id)}
