# V2 AMC Engine — Runbook

**Agent code:** V2
**Autonomy:** A2 (monitors and flags autonomously; renewal posting requires Service Manager approval)

---

## Normal operation

Runs on a scheduled daily basis to check AMC (Annual Maintenance Contract) statuses across the installed base. The agent identifies contracts nearing expiry, raises renewal proposals, registers new AMC contracts, and produces renewal pipeline reports. All renewals are flagged `NEEDS_APPROVAL` and held until the Service Manager approves.

Trigger commands: `check`, `renew`, `report`, `register`

---

## Key actions

Check AMC expiry status across installed base:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "v2_amc_engine", "action": "check", "payload": {"days_ahead": 60, "territory": "ALL"}}'
```

Raise a renewal proposal for a specific contract:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "v2_amc_engine", "action": "renew", "payload": {"contract_id": "AMC-2024-00123", "renewal_months": 12}}'
```

Register a new AMC contract against an installed unit:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "v2_amc_engine", "action": "register", "payload": {"serial_no": "SN-XYZ-001", "plan_code": "AMC-GOLD", "start_date": "2026-10-01", "months": 12}}'
```

Generate renewal pipeline report:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "v2_amc_engine", "action": "report", "payload": {"period": "next_60_days", "group_by": "territory"}}'
```

---

## Interpreting the output

| Field | Meaning |
|---|---|
| `contract_id` | Unique ID from AmcContract table |
| `installed_base_id` | Reference to InstalledBase unit |
| `expiry_date` | Contract expiry date |
| `days_to_expiry` | Days remaining; < 30 triggers urgent flag |
| `renewal_status` | `proposed` / `NEEDS_APPROVAL` / `approved` / `lapsed` |
| `renewal_value` | Proposed renewal amount in INR |
| `approval_ref` | Token issued when status = `NEEDS_APPROVAL` |
| `lapsed_count` | Number of contracts already expired without renewal |

---

## Rollback

To suspend renewal proposal posting and revert to dry-run mode:
```bash
curl -X PATCH http://localhost:8000/lanes/v2_amc_engine \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

Renewals in `NEEDS_APPROVAL` state are not committed to AmcContract until the Service Manager explicitly approves via the approval workflow. To cancel a pending renewal proposal:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "v2_amc_engine", "action": "cancel_renewal", "payload": {"approval_ref": "<token>"}}'
```

---

## Escalation

| Trigger | Notify |
|---|---|
| Contracts lapsed (expired without renewal) > 5% of active base | Service Manager + GM |
| Renewal approval pending for > 48 hours | Service Manager via alert |
| InstalledBase record missing AMC linkage for units > 1 year old | Service Manager + IT/Systems team |
