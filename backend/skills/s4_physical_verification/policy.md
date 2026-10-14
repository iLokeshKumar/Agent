# S4 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| Serial count matches registry exactly | Auto-approve; report generated |
| Missing serials detected | Store Manager review required before any registry write |
| Excess/unregistered serials detected | Store Manager review required; serials quarantined |
| Discrepancy > 5% of total count | Escalate to Regional Manager; freeze further stock movements |
| Run covers multiple locations | VALIDATION_ERROR; split into per-location runs |

## Constraints
1. The agent operates in read-only mode during comparison; no inventory record may be mutated during the run.
2. Serial conservation rate must be maintained at 100%; any shortfall is a P1 incident.
3. Verification reports are immutable once generated; corrections must be filed as new runs.
4. The agent must never auto-correct a mismatch; all corrections require human approval.
5. All verification runs must be logged with timestamp, operator ID, and location code.

## Data
- Source: serial_registry table, inventory_on_hand table, warehouse_location_master
- Output: physical_verification_reports table, discrepancy_exceptions queue
