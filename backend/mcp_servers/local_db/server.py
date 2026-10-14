"""Local-DB MCP server — typed tool access to the Yexis governance store.

WP-1 specifies this as the ONLY write path from an agent to the local
database. No free-form SQL; every tool has a fixed schema and a fixed
effect. The rule is structural: tools that don't exist here don't exist.

Tools by group
──────────────
ledger.*        append-only action ledger (audit trail)
  ledger.append(action, tool_name?, entity_type?, entity_id?,
                status?, error_code?, error_message?,
                task_id?, correlation_id?, input_ref?, output_ref?)
  ledger.query(correlation_id?, task_id?, limit?)

approvals.*     human-gate queue
  approvals.create(approval_type, requested_by, payload?,
                   task_id?)
  approvals.list(status?)
  approvals.decide(approval_id, decision, decided_by, reason?)

staging.*       Tally stage-and-approve buffer
  staging.insert(entity, books_txn_id, entries[])
  staging.list(entity?, status?, limit?)
  staging.set_status(staging_id, status, reason?, approved_by?)

watermarks.*    incremental-pull cursors
  watermarks.get(lane, source)
  watermarks.set(lane, source, cursor?, extra?)

locks.*         distributed advisory locks (prevents double-scheduling)
  locks.acquire(lock_key, holder, ttl_seconds?)
  locks.release(lock_key, holder)

Each function returns a plain dict or list[dict] — no ORM objects.
Callers that need raw error information receive a {"error": str} dict
rather than an exception — MCP tools must not raise.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_LOCK_TTL_DEFAULT = 300  # 5 min


def _session():
    from app.core.db import SessionLocal
    return SessionLocal()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── Ledger ──────────────────────────────────────────────────────────


def ledger_append(
    action: str,
    tool_name: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    status: str = "SUCCESS",
    error_code: str | None = None,
    error_message: str | None = None,
    task_id: str | None = None,
    correlation_id: str | None = None,
    input_ref: str | None = None,
    output_ref: str | None = None,
) -> dict:
    """Append one immutable row to the action ledger.

    The ledger is append-only: there is no update or delete. A
    correction is a new row that references the original via
    ``correlation_id``.
    """
    from app.models.action_ledger import ActionLedger

    db = _session()
    try:
        row = ActionLedger(
            action=action,
            tool_name=tool_name,
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
            task_id=uuid.UUID(task_id) if task_id else None,
            correlation_id=uuid.UUID(correlation_id) if correlation_id else None,
            input_ref=input_ref,
            output_ref=output_ref,
        )
        db.add(row)
        db.commit()
        return {"id": str(row.id), "appended": True}
    except Exception as exc:
        logger.error("ledger.append failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


def ledger_query(
    correlation_id: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query recent ledger rows, newest first, capped at 500."""
    from app.models.action_ledger import ActionLedger
    from sqlalchemy import select

    limit = min(max(limit, 1), 500)
    db = _session()
    try:
        q = (
            select(ActionLedger)
            .order_by(ActionLedger.created_at.desc())
            .limit(limit)
        )
        if correlation_id:
            q = q.where(ActionLedger.correlation_id == uuid.UUID(correlation_id))
        if task_id:
            q = q.where(ActionLedger.task_id == uuid.UUID(task_id))
        rows = list(db.execute(q).scalars())
        return [
            {
                "id": str(r.id),
                "action": r.action,
                "tool_name": r.tool_name,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "status": r.status,
                "error_code": r.error_code,
                "error_message": r.error_message,
                "task_id": str(r.task_id) if r.task_id else None,
                "correlation_id": str(r.correlation_id) if r.correlation_id else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("ledger.query failed: %s", exc)
        return [{"error": str(exc)}]
    finally:
        db.close()


# ─── Approvals ───────────────────────────────────────────────────────


def approvals_create(
    approval_type: str,
    requested_by: str,
    payload: dict | None = None,
    task_id: str | None = None,
) -> dict:
    """Create a new PENDING approval request."""
    from app.models.approval import Approval

    db = _session()
    try:
        row = Approval(
            approval_type=approval_type,
            requested_by=requested_by,
            payload=payload or {},
            task_id=uuid.UUID(task_id) if task_id else None,
            status="PENDING",
        )
        db.add(row)
        db.commit()
        return {"id": str(row.id), "status": "PENDING"}
    except Exception as exc:
        logger.error("approvals.create failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


def approvals_list(status: str | None = None) -> list[dict]:
    """List approvals. Default: PENDING only."""
    from app.models.approval import Approval
    from sqlalchemy import select

    db = _session()
    try:
        q = select(Approval).order_by(Approval.requested_at.desc()).limit(200)
        if status:
            q = q.where(Approval.status == status)
        else:
            q = q.where(Approval.status == "PENDING")
        rows = list(db.execute(q).scalars())
        return [
            {
                "id": str(r.id),
                "approval_type": r.approval_type,
                "status": r.status,
                "requested_by": r.requested_by,
                "approved_by": r.approved_by,
                "reason": r.reason,
                "payload": r.payload,
                "task_id": str(r.task_id) if r.task_id else None,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("approvals.list failed: %s", exc)
        return [{"error": str(exc)}]
    finally:
        db.close()


def approvals_decide(
    approval_id: str,
    decision: str,
    decided_by: str,
    reason: str | None = None,
) -> dict:
    """Approve or reject a pending approval.

    decision: 'approve' | 'reject'
    """
    from app.models.approval import Approval
    from app.repository.approval_repository import ApprovalRepository

    if decision not in ("approve", "reject"):
        return {"error": "decision must be 'approve' or 'reject'"}

    db = _session()
    try:
        repo = ApprovalRepository(db)
        row = repo.get(uuid.UUID(approval_id))
        if row is None:
            return {"error": f"approval {approval_id} not found"}
        if row.status != "PENDING":
            return {"error": f"approval is already {row.status}"}

        if decision == "approve":
            repo.approve(row, approved_by=decided_by, reason=reason)
        else:
            repo.reject(row, approved_by=decided_by, reason=reason)

        db.commit()
        return {"id": approval_id, "status": row.status}
    except Exception as exc:
        logger.error("approvals.decide failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


# ─── Staging ─────────────────────────────────────────────────────────


def staging_insert(
    entity: str,
    books_txn_id: str,
    entries: list[dict],
) -> dict:
    """Stage transaction entries for accountant review and Tally posting.

    Idempotent on (entity, books_txn_id): if rows already exist the
    call is a no-op and returns the existing row IDs.

    Each entry dict must have: tally_account, debit, credit, voucher_type.
    Optional: date, narration, reference.
    """
    from app.integrations.tally_gateway import TallyGateway, TallyGatewayError

    db = _session()
    try:
        rows = TallyGateway(db).stage_entries(entity, books_txn_id, entries)
        db.commit()
        return {
            "staged": len(rows),
            "ids": [str(r.id) for r in rows],
            "idempotent": False,
        }
    except TallyGatewayError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("staging.insert failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


def staging_list(
    entity: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """List staging voucher rows with optional entity + status filter."""
    from app.models.tally_staging import TallyStagingVoucher
    from sqlalchemy import select

    limit = min(max(limit, 1), 500)
    db = _session()
    try:
        q = (
            select(TallyStagingVoucher)
            .order_by(
                TallyStagingVoucher.created_at.desc(),
                TallyStagingVoucher.line_no,
            )
            .limit(limit)
        )
        if entity:
            q = q.where(TallyStagingVoucher.entity == entity)
        if status:
            q = q.where(TallyStagingVoucher.status == status)
        rows = list(db.execute(q).scalars())
        return [
            {
                "id": str(r.id),
                "entity": r.entity,
                "books_txn_id": r.books_txn_id,
                "line_no": r.line_no,
                "voucher_type": r.voucher_type,
                "voucher_date": r.voucher_date.isoformat() if r.voucher_date else None,
                "tally_account": r.tally_account,
                "debit": r.debit,
                "credit": r.credit,
                "status": r.status,
                "approved_by": r.approved_by,
                "tally_voucher_no": r.tally_voucher_no,
                "error_message": r.error_message,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("staging.list failed: %s", exc)
        return [{"error": str(exc)}]
    finally:
        db.close()


def staging_set_status(
    staging_id: str,
    status: str,
    reason: str | None = None,
    approved_by: str | None = None,
) -> dict:
    """Manually set the status of a staging row.

    status: STAGED | APPROVED | REJECTED | POST_FAILED

    For approval use staging_insert + the TallyGateway approve() path.
    This tool is for administrative corrections (e.g. reset a
    POST_FAILED row back to APPROVED to retry).
    """
    from app.models.tally_staging import TallyStagingVoucher
    from sqlalchemy import select

    VALID = {"STAGED", "APPROVED", "REJECTED", "POST_FAILED"}
    if status not in VALID:
        return {"error": f"status must be one of {sorted(VALID)}"}

    db = _session()
    try:
        row = db.execute(
            select(TallyStagingVoucher).where(
                TallyStagingVoucher.id == uuid.UUID(staging_id)
            )
        ).scalars().first()
        if row is None:
            return {"error": f"staging row {staging_id} not found"}
        row.status = status
        if reason:
            row.error_message = reason
        if approved_by:
            row.approved_by = approved_by
            row.approved_at = _utcnow()
        db.commit()
        return {"id": staging_id, "status": status}
    except Exception as exc:
        logger.error("staging.set_status failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


# ─── Watermarks ──────────────────────────────────────────────────────


def watermarks_get(lane: str, source: str) -> dict:
    """Return the incremental-pull cursor for a lane+source pair."""
    from app.models.sync_watermark import SyncWatermark
    from sqlalchemy import select

    db = _session()
    try:
        row = db.execute(
            select(SyncWatermark).where(
                SyncWatermark.lane == lane,
                SyncWatermark.source == source,
            )
        ).scalars().first()
        if row is None:
            return {"lane": lane, "source": source, "cursor": None, "extra": {}}
        return {
            "lane": row.lane,
            "source": row.source,
            "cursor": row.cursor,
            "extra": row.extra or {},
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    except Exception as exc:
        logger.error("watermarks.get failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


def watermarks_set(
    lane: str,
    source: str,
    cursor: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Upsert the incremental-pull cursor for a lane+source pair."""
    from app.models.sync_watermark import SyncWatermark
    from sqlalchemy import select

    db = _session()
    try:
        row = db.execute(
            select(SyncWatermark).where(
                SyncWatermark.lane == lane,
                SyncWatermark.source == source,
            )
        ).scalars().first()
        if row is None:
            row = SyncWatermark(
                id=uuid.uuid4(), lane=lane, source=source
            )
            db.add(row)
        row.cursor = cursor
        if extra is not None:
            row.extra = dict(extra)
        db.commit()
        return {
            "lane": row.lane,
            "source": row.source,
            "cursor": row.cursor,
            "extra": row.extra or {},
        }
    except Exception as exc:
        logger.error("watermarks.set failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


# ─── Locks ───────────────────────────────────────────────────────────
# Advisory distributed lock: prevents two agent instances from running the
# same scheduled task concurrently. Implemented on the action ledger rather
# than a separate table to avoid schema churn — a lock is a "LOCK_ACQUIRE"
# action row; release is a "LOCK_RELEASE" with the same key.

def locks_acquire(
    lock_key: str,
    holder: str,
    ttl_seconds: int = _LOCK_TTL_DEFAULT,
) -> dict:
    """Acquire an advisory lock for a named resource.

    Returns {"acquired": True} on success or {"acquired": False,
    "held_by": str, "expires_at": iso} if already locked.

    Lock is advisory: callers must check the return value. The lock
    auto-expires after ttl_seconds (default 5 min) to handle crashes.
    """
    from app.models.action_ledger import ActionLedger
    from sqlalchemy import select
    from datetime import timedelta

    db = _session()
    try:
        now = _utcnow()
        expires_at = now + timedelta(seconds=ttl_seconds)

        # Check for an unexpired lock held by another holder.
        active = db.execute(
            select(ActionLedger)
            .where(
                ActionLedger.action == "LOCK_ACQUIRE",
                ActionLedger.entity_id == lock_key,
                ActionLedger.status == "SUCCESS",
            )
            .order_by(ActionLedger.created_at.desc())
            .limit(1)
        ).scalars().first()

        if active and active.tool_name != holder:
            # Check if the lock has a recorded release.
            released = db.execute(
                select(ActionLedger)
                .where(
                    ActionLedger.action == "LOCK_RELEASE",
                    ActionLedger.entity_id == lock_key,
                    ActionLedger.created_at > active.created_at,
                )
                .limit(1)
            ).scalars().first()

            if not released:
                # Compute expiry from the acquire row's output_ref
                lock_expires = None
                if active.output_ref:
                    try:
                        lock_expires = datetime.fromisoformat(active.output_ref)
                    except ValueError:
                        pass
                # If the lock has expired (TTL elapsed), allow override.
                if lock_expires and lock_expires > now:
                    return {
                        "acquired": False,
                        "held_by": active.tool_name,
                        "expires_at": lock_expires.isoformat(),
                    }

        # Record the acquire.
        row = ActionLedger(
            action="LOCK_ACQUIRE",
            tool_name=holder,
            entity_type="lock",
            entity_id=lock_key,
            status="SUCCESS",
            output_ref=expires_at.isoformat(),
        )
        db.add(row)
        db.commit()
        return {"acquired": True, "expires_at": expires_at.isoformat()}
    except Exception as exc:
        logger.error("locks.acquire failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


def locks_release(lock_key: str, holder: str) -> dict:
    """Release a previously acquired advisory lock."""
    from app.models.action_ledger import ActionLedger

    db = _session()
    try:
        row = ActionLedger(
            action="LOCK_RELEASE",
            tool_name=holder,
            entity_type="lock",
            entity_id=lock_key,
            status="SUCCESS",
        )
        db.add(row)
        db.commit()
        return {"released": True}
    except Exception as exc:
        logger.error("locks.release failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()


# ─── Tool catalogue ───────────────────────────────────────────────────


def list_tools() -> list[dict]:
    """MCP tool catalogue — all tools callable by agents."""
    return [
        # Ledger
        {"name": "ledger.append", "write": True,
         "desc": "Append one immutable row to the action audit ledger"},
        {"name": "ledger.query", "write": False,
         "desc": "Query ledger rows by correlation_id or task_id"},
        # Approvals
        {"name": "approvals.create", "write": True,
         "desc": "Create a new PENDING approval request"},
        {"name": "approvals.list", "write": False,
         "desc": "List approvals; defaults to PENDING"},
        {"name": "approvals.decide", "write": True,
         "desc": "Approve or reject a pending approval"},
        # Staging
        {"name": "staging.insert", "write": True,
         "desc": "Stage transaction entries for accountant review"},
        {"name": "staging.list", "write": False,
         "desc": "List staging rows with entity/status filter"},
        {"name": "staging.set_status", "write": True,
         "desc": "Manually set staging row status (admin corrections only)"},
        # Watermarks
        {"name": "watermarks.get", "write": False,
         "desc": "Get the incremental-pull cursor for a lane+source pair"},
        {"name": "watermarks.set", "write": True,
         "desc": "Upsert the incremental-pull cursor for a lane+source pair"},
        # Locks
        {"name": "locks.acquire", "write": True,
         "desc": "Acquire an advisory distributed lock"},
        {"name": "locks.release", "write": True,
         "desc": "Release a previously acquired advisory lock"},
    ]
