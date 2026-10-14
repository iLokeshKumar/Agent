# F5 Policy

## Approval Gates

| Condition                          | Gate              |
|------------------------------------|-------------------|
| Any output used for external comms | Finance Manager   |
| Forecast triggers overdraft alert  | MD notification   |

## Constraints

1. Forecast output is advisory — no payment or disbursement may be triggered by F5 alone
2. Collection probability parameters may only be changed by Finance Controller via config change + gate re-sign
3. `opening_balance` must be provided by the caller — F5 does not read bank balances directly
4. Actuals reconciliation (`update_actuals`) requires Finance Manager approval before KPI update

## Data

- Input: `dealer_mart.overdue_buckets_json`, `kpi_snapshot.promise_reliability`, invoice cache payables
- Output: `cash_forecast` table (entity + period + week_offset composite key, idempotent via `db.merge()`)
