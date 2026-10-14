# S1 GRN Serialisation — Agent Runbook

Suite: Stores | Autonomy: A3

## Purpose
Captures serial numbers from inbound GRN. Validates every serial is new
(no duplicate). Creates `SerialRegistry` rows; updates Zoho Books item
receipt. Conservation rule: #GRN serials = #sold + #in_stock + other.

## Normal operation
1. Goods arrive; store staff uploads GRN document or scans via API
2. S1 extracts serials from document (PDF / structured input)
3. Checks `serial_registry` for duplicates (raises error on duplicate)
4. Creates `SerialRegistry` rows with `status=in_stock`
5. Creates `CustodyEvent` (issue) for record
6. Triggers Zoho Books item receipt via MCP

## KPI target
Serial conservation rate: 100% (no mystery serials).
Monitor: `SELECT value FROM kpi_snapshot WHERE kpi_id='serial_conservation_rate' ORDER BY period DESC LIMIT 5;`

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Duplicate serial error | Vendor re-using serial or re-GRN | Check `serial_registry` for existing row; resolve with P3 |
| Serials not in Zoho | Zoho item receipt failed | Check `action_ledger`; re-trigger via P4 |
| Conservation rate < 100% | Serials in unknown status | Query `serial_registry` for non-standard status |
