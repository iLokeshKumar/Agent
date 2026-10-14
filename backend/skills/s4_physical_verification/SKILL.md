# Physical Verification — S4
**Version:** 1.0.0  
**Autonomy:** A2  
**Go-live:** 2026-09-11  
**Primary KPI:** `serial_conservation_rate`  
**KPI Target:** 100%

## Purpose
Performs scheduled physical stock counts by cross-referencing on-hand serial numbers against the live serial registry. Detects and reports discrepancies (excess, missing, unregistered serials) without modifying the registry autonomously. Ensures every serial number present in the store matches the system record before any entry is altered.

## Trigger
Triggered on a scheduled cycle (daily/weekly) or manually initiated by a Store Manager via the operations dashboard.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| load_stock_snapshot | Pull current on-hand serial list from inventory | A2 |
| compare_to_registry | Diff physical serials against serial registry records | A2 |
| flag_discrepancy | Raise EXCESS_SERIAL or MISSING_SERIAL exception on mismatch | A2 |
| generate_verification_report | Produce a reconciliation summary with match/gap counts | A2 |
| escalate_to_store_manager | Route unresolved discrepancies for human sign-off | A2 (human gate) |

## Constraints
- Never write to the serial registry without explicit Store Manager approval.
- Never suppress or auto-resolve a serial mismatch; every gap must be surfaced.
- Never combine verification runs — each run covers a single warehouse location.
- Serial conservation rate must remain 100%; any drop triggers immediate escalation.
- Read-only access to all inventory tables during the comparison phase.
