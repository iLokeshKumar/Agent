# F2 Scheme Claims — Agent Runbook

Suite: Finance | Autonomy: A2

## Purpose
Validates and submits Samsung scheme claims (volume rebates, sell-through
incentives). Cross-checks sales data against scheme circulars; drafts the
claim package; routes for human approval before submission to Samsung portal.

## Normal operation
1. Samsung scheme circular arrives via email (DocIndex)
2. F2 extracts scheme parameters and validates against `serial_registry` (sold)
3. Computes eligible claim amount deterministically
4. Routes draft to Finance Manager for review (A1 proposal)
5. On approval, submits to Samsung portal and logs to `action_ledger`

## Approval gate
All claim submissions require Finance Manager sign-off regardless of amount.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Claim amount mismatch | Serial status not updated | Verify `serial_registry` sold status for the claim period |
| Circular not parsed | PDF extraction failed | Check `doc_index` `confidence` score; review extract_json |
| Claim rejected by Samsung | Date/serial format error | Review claim package in `doc_index`; resubmit manually |

## KPI target
Claim accuracy: ≥ 95% (approved / submitted).
Monitor: `SELECT value FROM kpi_snapshot WHERE kpi_id='f2_claim_accuracy_pct' ORDER BY period DESC LIMIT 5;`
