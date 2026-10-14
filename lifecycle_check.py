"""P1 purchase-request lifecycle check.

Proves the four scenarios that define P1 v1:

    1. Under budget  -> COMPLETED + PO
    2. Over budget   -> AWAITING_APPROVAL + PENDING approval, no PO
    3. Approve       -> COMPLETED + PO
    4. Reject        -> FAILED, no PO

Run against a live server:  python lifecycle_check.py
Exits non-zero if any scenario fails.
"""

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
REQUESTED_BY = "admin"
APPROVER = "lokesh"
TIMEOUT = 30  # seconds to wait for the worker to settle a task

results = []


def submit(quantity: int) -> str:
    """Create a purchase_request task and return its id."""
    response = httpx.post(
        f"{BASE}/tasks",
        json={
            "task_type": "purchase_request",
            "requested_by": REQUESTED_BY,
            "payload": {
                "items": [
                    {
                        "item": "Laptop",
                        "quantity": quantity,
                        "unit_price": 75000,
                    }
                ],
                "vendor": "ABC Technologies",
                "budget_limit": 100000,
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["task_id"]


def wait_for(task_id: str, statuses: set[str]) -> dict:
    """Poll a task until it reaches one of `statuses`."""
    deadline = time.time() + TIMEOUT

    while time.time() < deadline:
        task = httpx.get(f"{BASE}/tasks/{task_id}", timeout=30).json()

        if task["status"] in statuses:
            return task

        time.sleep(0.5)

    return httpx.get(f"{BASE}/tasks/{task_id}", timeout=30).json()


def approval_for(task_id: str) -> dict | None:
    """Find the most recent approval belonging to a task."""
    approvals = httpx.get(f"{BASE}/approvals", timeout=30).json()

    for approval in approvals:
        if approval["task_id"] == task_id:
            return approval

    return None


def po_of(task: dict) -> str | None:
    return (task.get("result") or {}).get("output", {}).get("po_number")


def check(name: str, passed: bool, detail: str) -> None:
    results.append((name, passed))
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {name}")
    print(f"       {detail}")


# ── Scenario 1: under budget ──────────────────────────────────────────
print("\n=== 1. Under budget (75,000 <= 100,000) ===")
task_id = submit(quantity=1)
task = wait_for(task_id, {"COMPLETED", "FAILED", "VALIDATION_ERROR"})
po = po_of(task)

check(
    "under budget -> COMPLETED + PO",
    task["status"] == "COMPLETED" and bool(po),
    f"task={task_id} status={task['status']} po={po} "
    f"error={task.get('error')}",
)

# ── Scenario 2: over budget ───────────────────────────────────────────
print("\n=== 2. Over budget (150,000 > 100,000) ===")
approve_task_id = submit(quantity=2)
task = wait_for(
    approve_task_id,
    {"AWAITING_APPROVAL", "COMPLETED", "FAILED", "VALIDATION_ERROR"},
)
approval = approval_for(approve_task_id)

check(
    "over budget -> AWAITING_APPROVAL + PENDING approval, no PO",
    (
        task["status"] == "AWAITING_APPROVAL"
        and approval is not None
        and approval["status"] == "PENDING"
        and po_of(task) is None
    ),
    f"task={approve_task_id} status={task['status']} "
    f"approval={(approval or {}).get('id')} "
    f"approval_status={(approval or {}).get('status')} "
    f"po={po_of(task)}",
)

# ── Scenario 3: approve ───────────────────────────────────────────────
print("\n=== 3. Approve the over-budget request ===")
if approval:
    response = httpx.post(
        f"{BASE}/approvals/{approval['id']}/approve",
        json={"approved_by": APPROVER, "reason": "Budget increase agreed"},
        timeout=30,
    )
    print(f"       POST approve -> {response.status_code} {response.json()}")

    task = wait_for(
        approve_task_id, {"COMPLETED", "FAILED", "VALIDATION_ERROR"}
    )
    decided = httpx.get(
        f"{BASE}/approvals/{approval['id']}", timeout=30
    ).json()
    po = po_of(task)

    check(
        "approve -> task COMPLETED + PO, approval APPROVED",
        (
            task["status"] == "COMPLETED"
            and bool(po)
            and decided["status"] == "APPROVED"
        ),
        f"task={approve_task_id} status={task['status']} po={po} "
        f"approval={decided['status']} by={decided['approved_by']} "
        f"error={task.get('error')}",
    )
else:
    check("approve -> task COMPLETED + PO", False, "no approval to approve")

# ── Scenario 4: reject ────────────────────────────────────────────────
print("\n=== 4. Reject a second over-budget request ===")
reject_task_id = submit(quantity=2)
task = wait_for(
    reject_task_id,
    {"AWAITING_APPROVAL", "COMPLETED", "FAILED", "VALIDATION_ERROR"},
)
approval = approval_for(reject_task_id)

if approval and task["status"] == "AWAITING_APPROVAL":
    response = httpx.post(
        f"{BASE}/approvals/{approval['id']}/reject",
        json={"approved_by": APPROVER, "reason": "Over budget, denied"},
        timeout=30,
    )
    print(f"       POST reject -> {response.status_code} {response.json()}")

    task = wait_for(reject_task_id, {"FAILED", "COMPLETED"})
    decided = httpx.get(
        f"{BASE}/approvals/{approval['id']}", timeout=30
    ).json()

    check(
        "reject -> task FAILED, approval REJECTED, no PO",
        (
            task["status"] == "FAILED"
            and decided["status"] == "REJECTED"
            and po_of(task) is None
        ),
        f"task={reject_task_id} status={task['status']} "
        f"approval={decided['status']} po={po_of(task)} "
        f"error={task.get('error')}",
    )
else:
    check(
        "reject -> task FAILED, no PO",
        False,
        f"task never reached AWAITING_APPROVAL (status={task['status']})",
    )

# ── Summary ───────────────────────────────────────────────────────────
print("\n=== Summary ===")
for name, passed in results:
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")

failed = [name for name, passed in results if not passed]
print(f"\n{len(results) - len(failed)}/{len(results)} scenarios passed")

print("\nTask ids for audit queries:")
print(f"  under budget : {task_id}")
print(f"  approved     : {approve_task_id}")
print(f"  rejected     : {reject_task_id}")

sys.exit(1 if failed else 0)
