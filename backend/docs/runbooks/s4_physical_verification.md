# S4 Physical Verification — Runbook

**Agent code:** S4
**Autonomy:** A2 (executes autonomously; reconcile discrepancies require Store Manager review)

---

## Normal operation

Triggered by Store Manager at the start of a physical stock count cycle. The agent coordinates counting batches, records serial-level scan data, reconciles system stock vs physical count, and produces a variance report. Approval is required before posting the final reconciled quantities to inventory.

Trigger commands: `start`, `record`, `reconcile`, `approve`, `report`

---

## Key actions

Start a new physical count batch:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "s4_physical_verification", "action": "start", "payload": {"location_code": "WH-BLR-01", "count_date": "2026-09-11"}}'
```

Record scanned serials for a batch:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "s4_physical_verification", "action": "record", "payload": {"batch_id": "PCB-001", "serials": ["SN001", "SN002", "SN003"]}}'
```

Reconcile system vs physical count:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "s4_physical_verification", "action": "reconcile", "payload": {"batch_id": "PCB-001"}}'
```

Generate variance report:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "s4_physical_verification", "action": "report", "payload": {"batch_id": "PCB-001", "format": "summary"}}'
```

---

## Interpreting the output

| Field | Meaning |
|---|---|
| `batch_id` | Unique ID for this physical count batch (PhysicalCountBatch) |
| `system_qty` | Quantity recorded in SerialRegistry before count |
| `physical_qty` | Quantity recorded during physical scan |
| `variance` | `physical_qty - system_qty`; negative = shrinkage |
| `variance_pct` | Percentage variance; flag if > 1% |
| `status` | `open` / `counted` / `reconciled` / `approved` |
| `serials_missing` | Serials in system but not scanned |
| `serials_extra` | Serials scanned but not in system |
| `needs_approval` | `true` when reconcile produces non-zero variance |

---

## Rollback

To suspend the agent and prevent posting reconciled quantities:
```bash
curl -X PATCH http://localhost:8000/lanes/s4_physical_verification \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

To revert a batch to `open` state (before approval is posted):
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "s4_physical_verification", "action": "revert", "payload": {"batch_id": "PCB-001"}}'
```

Physical count records in SerialRegistry are not altered until `approve` is explicitly called by the Store Manager.

---

## Escalation

| Trigger | Notify |
|---|---|
| `variance_pct` > 2% on any SKU | Store Manager + Finance Manager immediately |
| Serials found in physical count that do not exist in SerialRegistry | Store Manager + IT/Systems team |
| Batch stuck in `counted` state for > 24 hours without reconcile | Store Manager |
