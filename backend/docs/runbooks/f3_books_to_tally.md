# F3 Books→Tally — Agent Runbook

Suite: Finance | Autonomy: A3 | Go-live: 2026-09-09

## Purpose
One-way staging of day-book transactions from Zoho Books to Tally Prime.
Single-write rule: each transaction is written to Tally exactly once.
The nightly TB reconciliation (`tb_reconciliation`) is the drift tripwire.

## Normal operation
1. F3 runs after day-book close (configurable, default 21:00)
2. Fetches new transactions from Zoho Books since last watermark
3. Maps accounts via `ledger_map`, godowns via `godown_map`, cost centres via `costcentre_map`
4. Posts XML batch to Tally via port 9000
5. Validates Tally response; on success, stamps watermark
6. At 02:00, nightly TB reconciliation compares Books vs Tally TB

## Drift handling
- If `drift_amount > 0` in `tb_reconciliation`, F3 is blocked from the next cycle
- Alert goes to Finance Manager
- Human reviews `drift_lines_json` to identify the mismatch
- On resolution, set `status = 'resolved'` with `resolved_note`

## Manual sync trigger
```bash
curl -X POST http://localhost:8000/agents/f3/run \
  -H "Content-Type: application/json" \
  -d '{"entity": "YEXIS-EL", "dry_run": false}'
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Tally connection refused | Tally not running or port blocked | Check Tally is running; verify port 9000 in firewall |
| Drift on TB reconciliation | Mapping gap or rounding | Check `drift_lines_json`; update `ledger_map` if needed |
| Duplicate vouchers | Watermark corruption | Check `sync_watermark` cursor; do NOT rerun without verifying Tally |
| Missing ledger | New account added to Books | Add row to `ledger_map` table; re-run |

## KPI target
`f3_books_tally_drift_hrs` = 0 (zero drift at all times).
