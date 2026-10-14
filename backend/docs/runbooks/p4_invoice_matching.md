# P4 Invoice Matching — Agent Runbook

Suite: Purchase | Autonomy: A3 | Go-live: 2026-09-09

## Purpose
3-way match: vendor invoice vs PO vs GRN. On FULLY_MATCHED, creates
payment entry in Zoho Books autonomously. Partial/mismatch cases go to
human review queue.

## Match statuses
- `FULLY_MATCHED` — A3 autonomous payment entry
- `PARTIAL_MATCH` — A1 proposal, human reviews
- `NO_MATCH` — Human resolves
- `DUPLICATE` — Rejected, logged to action_ledger

## KPI target
Auto-match rate: ≥ 95%.
Monitor: `SELECT value FROM kpi_snapshot WHERE kpi_id='p4_auto_match_rate' ORDER BY period DESC LIMIT 5;`

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Match rate below 95% | GRN qty/price variance | Review `action_ledger` for PARTIAL_MATCH reasons |
| Duplicate flagged incorrectly | Invoice number format change | Update dedup logic in p4_invoice_matching.py |
| Payment entry not created | Zoho API error | Check task result in `task` table |
| Invoice not received | Email not parsed | Check `doc_index` for doc_class=vendor_invoice |
