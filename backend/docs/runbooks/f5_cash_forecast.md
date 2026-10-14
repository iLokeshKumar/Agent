# F5 Cash Forecast — Runbook

**Agent code:** F5  
**Autonomy:** A1 (advisory — never triggers payments)

---

## Normal operation

F5 runs nightly after the mart chain to generate a 13-week rolling cash
position forecast for both entities. Results are persisted to `cash_forecast`
table and surfaced via D1 KPI queries.

**Manual on-demand forecast:**
```
POST /api/agents/run
{
  "agent_code": "F5",
  "payload": {
    "entity": "YEXIS-EL",
    "action": "forecast",
    "horizon_weeks": 13,
    "opening_balance": 5000000
  }
}
```

`opening_balance` is optional (defaults to 0). Finance team should provide
the actual opening bank balance for accurate net position.

---

## Reading the forecast

```
POST /api/agents/run
{
  "agent_code": "F5",
  "payload": {
    "entity": "YEXIS-EL",
    "action": "report"
  }
}
```

Returns week-by-week inflow, outflow, net position, and confidence %.

---

## Interpreting the output

| Field                  | Meaning                                                         |
|------------------------|-----------------------------------------------------------------|
| `week_1_net`           | Expected net cash change in 7 days — most reliable week        |
| `13_week_cumulative_net` | Total net over 13 weeks (±35% accuracy beyond week 4)        |
| `confidence_pct`       | Decreases linearly — week 1 ≈ 70%, week 13 ≈ 35%             |
| `total_ar_pool`        | Total AR across all aging buckets before collection decay      |
| `total_ap_pool`        | Total open vendor payables to be spread over 4 weeks          |

---

## Adjusting collection probabilities

Collection probability is hardcoded in `f5_cash_forecast.py:BUCKET_WEEKLY_COLLECT_PROB`.
To adjust:
1. Finance Controller proposes new values
2. Update constants in code
3. Re-sign gate record (`config/gates/f5_cash_forecast.json`)
4. Run golden set and redteam

---

## Rollback

Set lane dry_run=true:
```
PATCH /lanes/f5_cash_forecast
{"dry_run": true}
```
`CashForecast` rows are nightly-regenerated; disabling the lane stops new
forecasts but does not corrupt existing data.

---

## Escalation

- `week_1_net` < -₹20L → MD notification via morning digest
- `confidence_pct` < 40% on any week → Finance Manager review of AR aging data
