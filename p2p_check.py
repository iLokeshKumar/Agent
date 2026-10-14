"""Purchase-to-Pay vertical check.

Proves that one purchase request travels the whole workflow with no
manual database edits:

    PURCHASE REQUEST -> P1 -> PO -> S1 -> GRN -> P4 -> 3-way match
                     -> F3 -> books -> COMPLETE

Two views are used deliberately. HTTP, because that is how a client
submits work and reads a task back. SQL, because the point is not that
the agents returned SUCCESS — it is that the **business documents** exist
and agree with each other. Everything here reads; nothing here writes to
the database.

Run against a live server:  python p2p_check.py
Exits non-zero if any scenario fails.
"""

import subprocess
import sys
import time
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row


HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / "backend" / ".env"

# Agent messages use em dashes, and a piped stdout on Windows defaults to
# cp1252. Without this the report is readable on screen but mangled the
# moment anyone redirects it to a file.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
REQUESTED_BY = "admin"
APPROVER = "lokesh"
VENDOR = "ABC Technologies"

# Seconds to wait for the worker to settle a task. A chained step costs
# one poll interval, so a four-task chain settles well inside this.
TIMEOUT = 45
# Documents are written by the post-completion hook, which commits just
# after the task is marked COMPLETED. Short, separate budget for that.
DOCUMENT_TIMEOUT = 20

TERMINAL = {"COMPLETED", "FAILED", "VALIDATION_ERROR"}
SETTLED = TERMINAL | {"AWAITING_APPROVAL"}

# Invoice numbers and serial numbers are unique in the database, so each
# run tags its own.
RUN = uuid.uuid4().hex[:6].upper()
TODAY = datetime.now().strftime("%Y-%m-%d")

results = []


# ── Database: the auditor's view ─────────────────────────────────────


def _dsn() -> str:
    """Connection string from backend/.env — read-only use only."""
    values = {}

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        values[key.strip().upper()] = value.strip().strip("\"'")

    return (
        f"host={values.get('POSTGRES_HOST', 'localhost')} "
        f"port={values.get('POSTGRES_PORT', '5432')} "
        f"dbname={values.get('POSTGRES_DB', 'yexis')} "
        f"user={values.get('POSTGRES_USER', 'postgres')} "
        f"password={values.get('POSTGRES_PASSWORD', '')}"
    )


_connection = None


def connection():
    global _connection

    if _connection is None or _connection.closed:
        # autocommit so every read sees the worker's latest commit.
        _connection = psycopg.connect(_dsn(), autocommit=True)

    return _connection


def query(sql: str, **params) -> list[dict]:
    with connection().cursor(row_factory=dict_row) as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def one(sql: str, **params) -> dict | None:
    rows = query(sql, **params)

    return rows[0] if rows else None


def po_row(po_number: str | None) -> dict | None:
    return one(
        """
        SELECT purchase_number, vendor, status, total_amount, currency,
               task_id::text AS task_id,
               correlation_id::text AS correlation_id
        FROM purchase_orders
        WHERE purchase_number = %(n)s
        """,
        n=po_number,
    )


def po_status(po_number: str | None) -> str | None:
    return (po_row(po_number) or {}).get("status")


def grn_rows(po_number: str | None) -> list[dict]:
    return query(
        """
        SELECT grn_number, status, supplier, receiving_person
        FROM goods_receipts
        WHERE po_number = %(n)s
        ORDER BY created_at
        """,
        n=po_number,
    )


def grn_row(grn_number: str | None) -> dict | None:
    return one(
        """
        SELECT grn_number, po_number, status
        FROM goods_receipts
        WHERE grn_number = %(n)s
        """,
        n=grn_number,
    )


def invoice_row(invoice_number: str | None) -> dict | None:
    return one(
        """
        SELECT invoice_number, po_number, status, match_status,
               invoice_amount,
               goods_receipt_id::text AS goods_receipt_id
        FROM invoices
        WHERE invoice_number = %(n)s
        """,
        n=invoice_number,
    )


def serials(grn_number: str | None) -> list[str]:
    rows = query(
        """
        SELECT s.serial_number
        FROM goods_receipt_serials s
        JOIN goods_receipt_items i
          ON i.id = s.goods_receipt_item_id
        JOIN goods_receipts g
          ON g.id = i.goods_receipt_id
        WHERE g.grn_number = %(n)s
        ORDER BY s.serial_number
        """,
        n=grn_number,
    )

    return [row["serial_number"] for row in rows]


def child_tasks(correlation_id: str, task_type: str) -> list[dict]:
    """Tasks of one type in a workflow trace, oldest first."""
    return query(
        """
        SELECT id::text AS task_id, status
        FROM tasks
        WHERE correlation_id = %(c)s
          AND task_type = %(t)s
        ORDER BY created_at
        """,
        c=correlation_id,
        t=task_type,
    )


# ── HTTP: the client's view ──────────────────────────────────────────


def submit(task_type: str, payload: dict) -> dict:
    """Create a task. Returns {task_id, correlation_id, ...}."""
    response = httpx.post(
        f"{BASE}/tasks",
        json={
            "task_type": task_type,
            "requested_by": REQUESTED_BY,
            "payload": payload,
        },
        timeout=30,
    )
    response.raise_for_status()

    return response.json()


def get_task(task_id: str) -> dict:
    return httpx.get(f"{BASE}/tasks/{task_id}", timeout=30).json()


def wait_for(
    task_id: str,
    statuses: set[str] = SETTLED,
    timeout: int = TIMEOUT,
) -> dict:
    """Poll a task until it reaches one of `statuses`."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        task = get_task(task_id)

        if task.get("status") in statuses:
            return task

        time.sleep(0.4)

    return get_task(task_id)


def wait_for_child(
    correlation_id: str,
    task_type: str,
    statuses: set[str] = SETTLED,
    timeout: int = TIMEOUT,
) -> dict | None:
    """Wait for a chained task of this type to appear and settle."""
    deadline = time.time() + timeout
    task_id = None

    while time.time() < deadline:
        rows = child_tasks(correlation_id, task_type)

        if rows:
            task_id = rows[-1]["task_id"]

            if rows[-1]["status"] in statuses:
                return get_task(task_id)

        time.sleep(0.4)

    return get_task(task_id) if task_id else None


def no_child(
    correlation_id: str,
    task_type: str,
    settle: float = 1.0,
) -> bool:
    """True if nothing of this type was spawned in the trace."""
    time.sleep(settle)

    return not child_tasks(correlation_id, task_type)


def wait_for_value(getter, expected, timeout: int = DOCUMENT_TIMEOUT):
    """Poll `getter` until it equals `expected`. Returns the last value."""
    deadline = time.time() + timeout
    value = getter()

    while value != expected and time.time() < deadline:
        time.sleep(0.4)
        value = getter()

    return value


def approval_for(task_id: str) -> dict | None:
    """The most recent approval belonging to a task."""
    approvals = httpx.get(f"{BASE}/approvals", timeout=30).json()

    for approval in approvals:
        if approval["task_id"] == task_id:
            return approval

    return None


def decide(approval_id: str, verdict: str, reason: str) -> dict:
    response = httpx.post(
        f"{BASE}/approvals/{approval_id}/{verdict}",
        json={"approved_by": APPROVER, "reason": reason},
        timeout=30,
    )
    response.raise_for_status()

    return response.json()


def output(task: dict | None) -> dict:
    return ((task or {}).get("result") or {}).get("output") or {}


def grn_of(task: dict | None) -> dict:
    return output(task).get("grn") or {}


def check(name: str, passed: bool, detail: str) -> None:
    results.append((name, passed))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    print(f"       {detail}")


# ── Scenario 1: one POST reaches the books ───────────────────────────

print(f"\nrun tag {RUN}")
print("\n=== 1. Happy path: one POST reaches the books ===")

INV_A = f"INV-{RUN}-A"

first = submit(
    "purchase_request",
    {
        "items": [
            {"item": "Laptop", "quantity": 2, "unit_price": 45000}
        ],
        "vendor": VENDOR,
        "budget_limit": 200000,
        # The tax invoice travels with the goods, so the request declares
        # it up front. This is what makes one POST enough.
        "supplier_invoice": {
            "invoice_number": INV_A,
            "invoice_amount": 90000,
            "invoice_date": TODAY,
            "items": [
                {"item": "Laptop", "quantity": 2, "unit_price": 45000}
            ],
        },
    },
)

trace_a = first["correlation_id"]
intake = wait_for(first["task_id"])
po_a = output(intake).get("po_number")
purchase_order = po_row(po_a)

check(
    "purchase_request -> COMPLETED + purchase_orders row",
    intake["status"] == "COMPLETED"
    and bool(po_a)
    and purchase_order is not None
    and purchase_order["total_amount"] == Decimal("90000")
    and purchase_order["vendor"] == VENDOR,
    f"task={first['task_id']} status={intake['status']} po={po_a} "
    f"stored={purchase_order and purchase_order['total_amount']} "
    f"error={intake.get('error')}",
)

receipt_task = wait_for_child(trace_a, "grn_request")
grn_a = grn_of(receipt_task).get("grn_number")
receipts_a = grn_rows(po_a)

check(
    "auto-spawned grn_request -> COMPLETED + goods_receipts row",
    (receipt_task or {}).get("status") == "COMPLETED"
    and bool(grn_a)
    and len(receipts_a) == 1
    and receipts_a[0]["grn_number"] == grn_a
    and receipts_a[0]["status"] == "COMPLETE",
    f"task={(receipt_task or {}).get('task_id')} "
    f"status={(receipt_task or {}).get('status')} grn={grn_a} "
    f"rows={[(r['grn_number'], r['status']) for r in receipts_a]}",
)

match_task = wait_for_child(trace_a, "invoice_matching")
match_a = output(match_task).get("match_result") or {}

check(
    "auto-spawned invoice_matching -> FULLY_MATCHED against stored PO + GRN",
    (match_task or {}).get("status") == "COMPLETED"
    and match_a.get("match_status") == "FULLY_MATCHED"
    and match_a.get("grn_number") == grn_a
    and match_a.get("po_amount") == 90000.0
    and match_a.get("item_match_basis") == "line_level",
    f"status={(match_task or {}).get('status')} "
    f"match={match_a.get('match_status')} "
    f"invoice={match_a.get('invoice_amount')} "
    f"po={match_a.get('po_amount')} "
    f"variance={match_a.get('amount_variance')} "
    f"basis={match_a.get('item_match_basis')}",
)

books_task = wait_for_child(trace_a, "books_to_tally")
reconciliation = output(books_task).get("reconciliation") or {}
books_summary = output(books_task).get("summary") or {}

check(
    "auto-spawned books_to_tally -> COMPLETED + balanced Tally XML",
    (books_task or {}).get("status") == "COMPLETED"
    and reconciliation.get("is_balanced") is True
    and books_summary.get("unmapped_count") == 0
    and books_summary.get("tally_voucher_count", 0) >= 1,
    f"status={(books_task or {}).get('status')} "
    f"debit={reconciliation.get('total_debit')} "
    f"credit={reconciliation.get('total_credit')} "
    f"balanced={reconciliation.get('is_balanced')} "
    f"unmapped={books_summary.get('unmapped_count')} "
    f"vouchers={books_summary.get('tally_voucher_count')}",
)

final_po_a = wait_for_value(lambda: po_status(po_a), "POSTED")
final_invoice_a = wait_for_value(
    lambda: (invoice_row(INV_A) or {}).get("status"), "POSTED"
)
invoice_a = invoice_row(INV_A) or {}

check(
    "documents close out: PO POSTED, invoice POSTED + matched + linked to the GRN",
    final_po_a == "POSTED"
    and final_invoice_a == "POSTED"
    and invoice_a.get("match_status") == "FULLY_MATCHED"
    and invoice_a.get("goods_receipt_id") is not None,
    f"po={po_a} status={final_po_a} invoice={INV_A} "
    f"status={final_invoice_a} match={invoice_a.get('match_status')} "
    f"grn_link={'yes' if invoice_a.get('goods_receipt_id') else 'no'}",
)

# ── Scenario 2: the invoice arrives later ────────────────────────────

print("\n=== 2. Deferred invoice: the chain parks after the GRN ===")

second = submit(
    "purchase_request",
    {
        "items": [
            {"item": "Monitor", "quantity": 3, "unit_price": 12000}
        ],
        "vendor": VENDOR,
        "budget_limit": 200000,
    },
)

trace_b = second["correlation_id"]
intake_b = wait_for(second["task_id"])
po_b = output(intake_b).get("po_number")
receipt_task_b = wait_for_child(trace_b, "grn_request")
grn_b = grn_of(receipt_task_b).get("grn_number")

# The PO reaching RECEIVED proves the post-completion hook has finished,
# so "nothing was spawned" below is a settled fact and not a race.
received_b = wait_for_value(lambda: po_status(po_b), "RECEIVED")
parked = no_child(trace_b, "invoice_matching")

check(
    "no invoice on file -> chain stops after the GRN, PO RECEIVED",
    intake_b["status"] == "COMPLETED"
    and (receipt_task_b or {}).get("status") == "COMPLETED"
    and bool(grn_b)
    and received_b == "RECEIVED"
    and parked,
    f"po={po_b} status={received_b} grn={grn_b} "
    f"invoice_matching_tasks={len(child_tasks(trace_b, 'invoice_matching'))} "
    f"books_tasks={len(child_tasks(trace_b, 'books_to_tally'))}",
)

INV_B = f"INV-{RUN}-B"

third = submit(
    "invoice_matching",
    {
        "po_number": po_b,
        "invoice_number": INV_B,
        "invoice_amount": 36000,
        # No grn_number: P4 finds the receipt on file for itself. No
        # invoice_date either — an undated invoice posts on the day it
        # reached the books.
        "items": [
            {"item": "Monitor", "quantity": 3, "unit_price": 12000}
        ],
    },
)

trace_c = third["correlation_id"]
match_task_b = wait_for(third["task_id"])
match_b = output(match_task_b).get("match_result") or {}
books_task_b = wait_for_child(trace_c, "books_to_tally")
posted_invoice_b = wait_for_value(
    lambda: (invoice_row(INV_B) or {}).get("status"), "POSTED"
)
posted_po_b = wait_for_value(lambda: po_status(po_b), "POSTED")

check(
    "posted invoice_matching -> matches the stored PO + GRN and chains to the books",
    match_task_b["status"] == "COMPLETED"
    and match_b.get("match_status") == "FULLY_MATCHED"
    and match_b.get("grn_number") == grn_b
    and (books_task_b or {}).get("status") == "COMPLETED"
    and (
        (output(books_task_b).get("reconciliation") or {})
        .get("is_balanced")
        is True
    )
    and posted_invoice_b == "POSTED"
    and posted_po_b == "POSTED",
    f"task={third['task_id']} status={match_task_b['status']} "
    f"match={match_b.get('match_status')} grn={match_b.get('grn_number')} "
    f"books={(books_task_b or {}).get('status')} "
    f"invoice={posted_invoice_b} po={posted_po_b} "
    f"error={match_task_b.get('error')}",
)

# ── Scenario 3: documents that were never raised ─────────────────────

print("\n=== 3. Refusals: a document that was never raised ===")

PO_NONE = f"PO-{RUN}-NONE"

unknown_receipt = submit(
    "grn_request",
    {
        "po_number": PO_NONE,
        "items": [{"item": "Laptop", "quantity": 1}],
    },
)
unknown_receipt_task = wait_for(unknown_receipt["task_id"])

check(
    "grn_request against an unknown PO -> VALIDATION_ERROR, no GRN written",
    unknown_receipt_task["status"] == "VALIDATION_ERROR"
    and "not found" in str(unknown_receipt_task.get("error"))
    and grn_rows(PO_NONE) == [],
    f"status={unknown_receipt_task['status']} "
    f"error={unknown_receipt_task.get('error')} "
    f"grn_rows={len(grn_rows(PO_NONE))}",
)

INV_C = f"INV-{RUN}-C"

unknown_po_invoice = submit(
    "invoice_matching",
    {
        "po_number": PO_NONE,
        "invoice_number": INV_C,
        "invoice_amount": 1000,
    },
)
unknown_po_task = wait_for(unknown_po_invoice["task_id"])

check(
    "invoice_matching against an unknown PO -> VALIDATION_ERROR, nothing registered",
    unknown_po_task["status"] == "VALIDATION_ERROR"
    and "not found" in str(unknown_po_task.get("error"))
    and invoice_row(INV_C) is None,
    f"status={unknown_po_task['status']} "
    f"error={unknown_po_task.get('error')} "
    f"invoice_row={invoice_row(INV_C)}",
)

# ── Scenario 4: no goods, no payment ─────────────────────────────────

print("\n=== 4. An invoice cannot outrun the goods ===")

INV_D = f"INV-{RUN}-D"

no_receipt = submit(
    "invoice_matching",
    {
        # A real purchase order, naming a receipt that is not on file.
        "po_number": po_a,
        "grn_number": f"GRN-{RUN}-NONE",
        "invoice_number": INV_D,
        "invoice_amount": 90000,
    },
)
no_receipt_task = wait_for(no_receipt["task_id"])

check(
    "invoice_matching with no goods receipt on file -> VALIDATION_ERROR, nothing registered",
    no_receipt_task["status"] == "VALIDATION_ERROR"
    and "before the goods arrive" in str(no_receipt_task.get("error"))
    and invoice_row(INV_D) is None,
    f"status={no_receipt_task['status']} "
    f"error={no_receipt_task.get('error')} "
    f"invoice_row={invoice_row(INV_D)}",
)

# ── Scenario 5: mismatch routes to the approval gate ─────────────────

print("\n=== 5. Mismatch routes to the approval gate ===")

fifth = submit(
    "purchase_request",
    {
        "items": [
            {"item": "Printer", "quantity": 2, "unit_price": 20000}
        ],
        "vendor": VENDOR,
        "budget_limit": 200000,
    },
)

trace_d = fifth["correlation_id"]
intake_c = wait_for(fifth["task_id"])
po_c = output(intake_c).get("po_number")
receipt_task_c = wait_for_child(trace_d, "grn_request")
grn_c = grn_of(receipt_task_c).get("grn_number")
wait_for_value(lambda: po_status(po_c), "RECEIVED")

INV_E = f"INV-{RUN}-E"

mismatch = submit(
    "invoice_matching",
    {
        "po_number": po_c,
        "invoice_number": INV_E,
        "invoice_amount": 45000,  # the PO says 40,000
        "invoice_date": TODAY,
        "items": [
            {"item": "Printer", "quantity": 2, "unit_price": 22500}
        ],
    },
)

trace_e = mismatch["correlation_id"]
paused = wait_for(mismatch["task_id"])
approval = approval_for(mismatch["task_id"])

check(
    "invoice != PO -> AWAITING_APPROVAL + PENDING INVOICE_MISMATCH, nothing posted",
    paused["status"] == "AWAITING_APPROVAL"
    and approval is not None
    and approval["status"] == "PENDING"
    and approval["approval_type"] == "INVOICE_MISMATCH"
    and (invoice_row(INV_E) or {}).get("match_status") is None
    and not child_tasks(trace_e, "books_to_tally"),
    f"task={mismatch['task_id']} status={paused['status']} "
    f"approval={(approval or {}).get('id')} "
    f"type={(approval or {}).get('approval_type')} "
    f"variance={(approval or {}).get('payload', {}).get('amount_variance')} "
    f"invoice_match={(invoice_row(INV_E) or {}).get('match_status')}",
)

if approval:
    decide(approval["id"], "approve", "Freight difference accepted")

    resumed = wait_for(mismatch["task_id"], TERMINAL)
    match_c = output(resumed).get("match_result") or {}
    books_task_c = wait_for_child(trace_e, "books_to_tally")
    posted_invoice_e = wait_for_value(
        lambda: (invoice_row(INV_E) or {}).get("status"), "POSTED"
    )
    invoice_e = invoice_row(INV_E) or {}

    check(
        "approved mismatch -> COMPLETED and posted, still recorded as a mismatch",
        resumed["status"] == "COMPLETED"
        and match_c.get("approved_despite_mismatch") is True
        and match_c.get("match_status") != "FULLY_MATCHED"
        and (books_task_c or {}).get("status") == "COMPLETED"
        and posted_invoice_e == "POSTED"
        and invoice_e.get("match_status") == match_c.get("match_status"),
        f"status={resumed['status']} match={match_c.get('match_status')} "
        f"approved={match_c.get('approved_despite_mismatch')} "
        f"books={(books_task_c or {}).get('status')} "
        f"invoice_status={posted_invoice_e} "
        f"stored_match={invoice_e.get('match_status')} "
        f"error={resumed.get('error')}",
    )
else:
    check(
        "approved mismatch -> COMPLETED and posted",
        False,
        "no approval was raised to approve",
    )

INV_F = f"INV-{RUN}-F"

rejected = submit(
    "invoice_matching",
    {
        "po_number": po_c,
        "invoice_number": INV_F,
        "invoice_amount": 60000,  # the PO still says 40,000
        "invoice_date": TODAY,
        "items": [
            {"item": "Printer", "quantity": 2, "unit_price": 30000}
        ],
    },
)

trace_f = rejected["correlation_id"]
paused_2 = wait_for(rejected["task_id"])
approval_2 = approval_for(rejected["task_id"])

if approval_2 and paused_2["status"] == "AWAITING_APPROVAL":
    decide(approval_2["id"], "reject", "Overbilled — send it back")

    failed = wait_for(rejected["task_id"], {"FAILED", "COMPLETED"})
    invoice_f = invoice_row(INV_F) or {}

    check(
        "rejected mismatch -> FAILED, no match recorded, nothing posted",
        failed["status"] == "FAILED"
        and invoice_f.get("match_status") is None
        and invoice_f.get("status") == "RECEIVED"
        and no_child(trace_f, "books_to_tally"),
        f"status={failed['status']} "
        f"invoice_status={invoice_f.get('status')} "
        f"invoice_match={invoice_f.get('match_status')} "
        f"books_tasks={len(child_tasks(trace_f, 'books_to_tally'))} "
        f"error={failed.get('error')}",
    )
else:
    check(
        "rejected mismatch -> FAILED, nothing posted",
        False,
        f"task never reached AWAITING_APPROVAL "
        f"(status={paused_2['status']})",
    )

# ── Scenario 6: exactly-once spawn ───────────────────────────────────

print("\n=== 6. Exactly-once spawn ===")

duplicates = query(
    """
    SELECT task_type,
           payload->'_chain'->>'parent_task_id' AS parent,
           count(*) AS spawns
    FROM tasks
    WHERE payload->'_chain' IS NOT NULL
    GROUP BY 1, 2
    HAVING count(*) > 1
    """
)

spawn_counts = {
    trace: len(child_tasks(trace, "grn_request"))
    for trace in (trace_a, trace_b, trace_d)
}
chained = one(
    "SELECT count(*) AS n FROM tasks WHERE source = 'chain'"
)

check(
    "one completed task never spawns the same child twice",
    duplicates == []
    and set(spawn_counts.values()) == {1},
    f"chained_tasks={chained['n']} "
    f"grn_requests_per_purchase_request="
    f"{sorted(spawn_counts.values())} "
    f"duplicate_parents={duplicates}",
)

# ── Scenario 7: serials, duplicates, over-receipt ────────────────────

print("\n=== 7. Serial capture, duplicate detection, over-receipt ===")

seventh = submit(
    "purchase_request",
    {
        "items": [
            {"item": "Router", "quantity": 2, "unit_price": 8000}
        ],
        "vendor": VENDOR,
        "budget_limit": 200000,
    },
)

trace_g = seventh["correlation_id"]
intake_d = wait_for(seventh["task_id"])
po_d = output(intake_d).get("po_number")
wait_for_child(trace_g, "grn_request")
wait_for_value(lambda: po_status(po_d), "RECEIVED")

SERIAL = f"SN-{RUN}-001"

# The gate: PO says 2, both arrived on the auto-GRN. A third unit is
# refused outright unless the business rule allows it.
blocked = submit(
    "grn_request",
    {
        "po_number": po_d,
        "items": [
            {
                "item": "Router",
                "received_quantity": 1,
            }
        ],
    },
)

blocked_task = wait_for(blocked["task_id"])
grn_count_before_extra = len(grn_rows(po_d))

check(
    "delivery beyond the ordered quantity -> VALIDATION_ERROR, no GRN written",
    blocked_task["status"] == "VALIDATION_ERROR"
    and "refused" in str(blocked_task.get("error"))
    and len(grn_rows(po_d)) == grn_count_before_extra,
    f"status={blocked_task['status']} "
    f"error={blocked_task.get('error')} "
    f"grn_rows={len(grn_rows(po_d))}",
)

extra = submit(
    "grn_request",
    {
        # A third unit turns up that nobody ordered, serialised, and
        # accepted under the explicit business rule that permits it.
        "po_number": po_d,
        "allow_over_receipt": True,
        "items": [
            {
                "item": "Router",
                "received_quantity": 1,
                "serial_numbers": [SERIAL],
            }
        ],
    },
)

extra_task = wait_for(extra["task_id"])
extra_grn = grn_of(extra_task).get("grn_number")
extra_status = wait_for_value(
    lambda: (grn_row(extra_grn) or {}).get("status"), "OVER_RECEIVED"
)
captured = wait_for_value(lambda: serials(extra_grn), [SERIAL])

check(
    "unexpected extra unit -> serial recorded, receipt flagged OVER_RECEIVED",
    extra_task["status"] == "COMPLETED"
    and bool(extra_grn)
    and extra_status == "OVER_RECEIVED"
    and captured == [SERIAL],
    f"grn={extra_grn} status={extra_status} serials={captured} "
    f"discrepancies="
    f"{[d.get('type') for d in grn_of(extra_task).get('discrepancies', [])]}",
)

repeat = submit(
    "grn_request",
    {
        "po_number": po_d,
        "allow_over_receipt": True,
        "items": [
            {
                "item": "Router",
                "received_quantity": 1,
                "serial_numbers": [SERIAL],
            }
        ],
    },
)

repeat_task = wait_for(repeat["task_id"])
repeat_grn = grn_of(repeat_task).get("grn_number")
repeat_status = wait_for_value(
    lambda: (grn_row(repeat_grn) or {}).get("status"),
    "HAS_DISCREPANCIES",
)
repeat_kinds = {
    d.get("type")
    for d in grn_of(repeat_task).get("discrepancies", [])
}

check(
    "serial already on file -> DUPLICATE_SERIAL flagged, receipt still recorded",
    repeat_task["status"] == "COMPLETED"
    and "DUPLICATE_SERIAL" in repeat_kinds
    and repeat_status == "HAS_DISCREPANCIES"
    and po_status(po_d) == "RECEIVED",
    f"grn={repeat_grn} status={repeat_status} "
    f"discrepancies={sorted(repeat_kinds)} "
    f"po={po_d} po_status={po_status(po_d)}",
)

# ── Scenario 8: P1's own lifecycle is untouched ──────────────────────

print("\n=== 8. P1's four-scenario lifecycle still passes ===")

lifecycle = subprocess.run(
    [sys.executable, str(HERE / "lifecycle_check.py")],
    cwd=str(HERE),
    capture_output=True,
    text=True,
    timeout=300,
)

tail = [
    line.strip()
    for line in lifecycle.stdout.splitlines()
    if line.strip().startswith(("PASS", "FAIL", "4/4", "3/4", "2/4", "1/4", "0/4"))
]

check(
    "lifecycle_check.py still passes all four P1 scenarios",
    lifecycle.returncode == 0,
    f"exit={lifecycle.returncode} " + " | ".join(tail[-5:]),
)

# ── Audit trail ──────────────────────────────────────────────────────

AUDIT_SQL = """
    SELECT t.task_type, t.status,
           po.purchase_number, po.status AS po_status,
           gr.grn_number, gr.status AS grn_status,
           i.invoice_number, i.status AS invoice_status, i.match_status
    FROM tasks t
    LEFT JOIN purchase_orders po ON po.task_id = t.id
    LEFT JOIN goods_receipts  gr ON gr.task_id = t.id
    LEFT JOIN invoices        i  ON i.task_id  = t.id
    WHERE t.correlation_id = %(c)s
    ORDER BY t.created_at
"""


def audit(correlation_id: str, label: str) -> None:
    rows = query(AUDIT_SQL, c=correlation_id)

    print(f"\n  {label}")
    print(f"  correlation {correlation_id}")
    print(
        f"    {'task':<18}{'status':<12}{'purchase order':<28}"
        f"{'goods receipt':<28}{'invoice':<34}"
    )

    for row in rows:
        po_cell = (
            f"{row['purchase_number']} ({row['po_status']})"
            if row["purchase_number"] else "-"
        )
        grn_cell = (
            f"{row['grn_number']} ({row['grn_status']})"
            if row["grn_number"] else "-"
        )
        invoice_cell = (
            f"{row['invoice_number']} ({row['invoice_status']}"
            f"/{row['match_status'] or 'unmatched'})"
            if row["invoice_number"] else "-"
        )

        print(
            f"    {row['task_type']:<18}{row['status']:<12}"
            f"{po_cell:<28}{grn_cell:<28}{invoice_cell:<34}"
        )


print("\n=== Audit: one purchase request, four documents, one trace ===")
audit(trace_a, "Scenario 1 — invoice attached, so the whole flow is one trace")

print(
    "\n  Scenario 2 posted its invoice as a separate request, so it is "
    "two traces:"
)
audit(trace_b, "purchase -> goods receipt")
audit(trace_c, "invoice -> books")

# ── Summary ──────────────────────────────────────────────────────────

print("\n=== Summary ===")
for name, passed in results:
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")

failed = [name for name, passed in results if not passed]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")

print("\nDocuments produced by this run:")
print(f"  scenario 1 : {po_a} / {grn_a} / {INV_A}")
print(f"  scenario 2 : {po_b} / {grn_b} / {INV_B}")
print(f"  scenario 5 : {po_c} / {grn_c} / {INV_E} (approved), {INV_F} (rejected)")
print(f"  scenario 7 : {po_d} / {extra_grn}, {repeat_grn} / serial {SERIAL}")

sys.exit(1 if failed else 0)
