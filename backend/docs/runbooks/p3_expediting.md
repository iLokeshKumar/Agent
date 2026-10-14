# P3 Expediting — Agent Runbook

Suite: Purchase | Autonomy: A2

## Purpose
Monitors open POs and chases vendors when delivery is late or at risk.
Logs every vendor promise/delay/delivery to `vendor_scorecard_event`.
This data feeds the nightly `vendor_scorecard` mart and `vendor_otif_pct` KPI.

## Normal operation
1. P3 scans open Zoho Books POs daily
2. For each PO past expected delivery: sends WhatsApp/email chase to vendor
3. Logs response as `VendorScorecardEvent` (confirm | delay | deliver)
4. On delivery confirmation: triggers S1 GRN

## KPI target
Vendor OTIF: ≥ 90%.
Monitor: `SELECT value FROM kpi_snapshot WHERE kpi_id='vendor_otif_pct' ORDER BY period DESC LIMIT 5;`

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| OTIF score low | Vendor consistently late | Escalate to GM; consider alternate vendor |
| Scorecard events not recorded | P3 not running | Check `task` table for p3 tasks |
| PO not found | PO in Zoho but not synced | Trigger Zoho cache refresh |
