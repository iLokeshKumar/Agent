# C2 Dispatch Tracker — Runbook

**Agent code:** C2
**Autonomy:** A2 (records and updates autonomously; overdue escalation sent without approval)

---

## Normal operation

Tracks outbound dispatches from warehouse to dealers. The agent records new dispatch events, accepts Proof of Delivery (POD) updates, flags overdue shipments, and generates pipeline reports. POD is considered complete only when both `pod_ref` (document reference) and `pod_ts` (timestamp) are present on the DispatchRecord.

Trigger commands: `record`, `update`, `report`, `overdue`

---

## Key actions

Record a new dispatch event:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c2_dispatch_tracker", "action": "record", "payload": {"invoice_no": "INV-2026-0089", "dealer_code": "DLR-MUM-012", "dispatch_date": "2026-09-11", "sku_qty": {"AC-1.5T-INV": 5, "WM-8KG-FL": 3}}}'
```

Update POD for a dispatch:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c2_dispatch_tracker", "action": "update", "payload": {"dispatch_id": "DSP-2026-0089", "pod_ref": "LR-BLUE-00456", "pod_ts": "2026-09-13T14:30:00Z"}}'
```

Generate dispatch pipeline report:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c2_dispatch_tracker", "action": "report", "payload": {"status": "in_transit", "territory": "WEST"}}'
```

Scan and flag overdue shipments:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c2_dispatch_tracker", "action": "overdue", "payload": {"sla_days": 5}}'
```

---

## Interpreting the output

| Field | Meaning |
|---|---|
| `dispatch_id` | Unique DispatchRecord ID |
| `invoice_no` | Source invoice reference |
| `dealer_code` | Receiving dealer identifier |
| `dispatch_date` | Date goods left warehouse |
| `pod_ref` | Proof of delivery document/LR number |
| `pod_ts` | Timestamp of confirmed delivery |
| `pod_complete` | `true` only when both `pod_ref` and `pod_ts` are set |
| `status` | `dispatched` / `in_transit` / `delivered` / `overdue` |
| `days_in_transit` | Days since dispatch; SLA default = 5 days |
| `overdue_count` | Shipments breaching SLA in current scan |

---

## Rollback

To pause dispatch recording and overdue scans:
```bash
curl -X PATCH http://localhost:8000/lanes/c2_dispatch_tracker \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

To correct a POD entry (e.g., wrong timestamp), re-call the `update` action with the corrected `pod_ref` and `pod_ts`. DispatchRecord preserves an audit trail of all updates.

---

## Escalation

| Trigger | Notify |
|---|---|
| Shipment overdue by > 3 days beyond SLA | Logistics Manager + Store Manager |
| POD missing for > 7 days post-dispatch | Finance Manager (outstanding liability) + Logistics Manager |
| Dispatch volume drop > 30% vs prior week | SCM Manager + GM |
