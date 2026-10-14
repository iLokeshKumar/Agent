# C3 Claims Tracker — Runbook

**Agent code:** C3
**Autonomy:** A2 (intakes and tracks autonomously; settlement postings require Finance/SCM Manager review)

---

## Normal operation

Manages the full lifecycle of supply-chain claims raised by dealers or internal teams. The agent accepts new claim intakes, updates claim status as they progress, and generates ageing reports. Supported claim types are: `short_supply`, `damage`, `price_diff`, `scheme`, `logistics`. Claims are stored in ScmClaim and tracked through their resolution lifecycle.

Trigger commands: `intake`, `update`, `report`

---

## Key actions

Intake a new supply chain claim:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c3_claims_tracker", "action": "intake", "payload": {"dealer_code": "DLR-DEL-007", "claim_type": "short_supply", "invoice_no": "INV-2026-0055", "claimed_qty": 3, "claimed_amount": 45000, "supporting_doc": "LR-DELHIVERY-789"}}'
```

Update the status of an existing claim:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c3_claims_tracker", "action": "update", "payload": {"claim_id": "CLM-2026-0031", "status": "under_review", "remarks": "Warehouse verification initiated"}}'
```

Generate claims ageing report:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c3_claims_tracker", "action": "report", "payload": {"claim_type": "ALL", "age_bucket": "30plus", "group_by": "claim_type"}}'
```

---

## Interpreting the output

| Field | Meaning |
|---|---|
| `claim_id` | Unique ScmClaim identifier |
| `claim_type` | One of: `short_supply`, `damage`, `price_diff`, `scheme`, `logistics` |
| `dealer_code` | Claimant dealer |
| `claimed_amount` | Amount claimed in INR |
| `approved_amount` | Amount approved after verification (may differ) |
| `status` | `raised` / `under_review` / `approved` / `rejected` / `settled` |
| `age_days` | Days since claim was raised |
| `age_bucket` | `0-15` / `16-30` / `31-60` / `60plus` |
| `settlement_ref` | Credit note or payment reference on settlement |

---

## Rollback

To pause claim intake and prevent new ScmClaim records from being created:
```bash
curl -X PATCH http://localhost:8000/lanes/c3_claims_tracker \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

Claims already raised can be moved back to `raised` status by the SCM Manager if a status update was made in error. Settlement postings to finance are always a manual step requiring Finance Manager sign-off and are never performed by the agent directly.

---

## Escalation

| Trigger | Notify |
|---|---|
| Claim aged > 30 days without resolution | Finance Manager + SCM Manager |
| `damage` or `logistics` claims exceeding INR 1,00,000 in a single intake | SCM Manager + GM immediately |
| Claim rejection disputed by dealer (status set to `disputed`) | Finance Manager + SCM Manager + relevant territory Sales Manager |
