# F5 — Cash Forecast

**Version:** 1.0.0  
**Autonomy:** A1 (propose only — forecast is advisory)  
**Go-live:** 2026-09-11  
**Primary KPI:** cash_forecast_accuracy  
**KPI Target:** ≥ 80 % week-1 accuracy (forecast vs actuals)

## Purpose

13-week rolling cash position forecast for YEXIS-EL and YEXIS-SL, combining:
- AR inflow: DealerMart aging buckets × empirical collection probability
- AP outflow: open vendor payables spread over 4-week lead time

Enables MD-level cash planning and early overdraft detection.

## Trigger

- Nightly scheduler (02:00) — `action=forecast`
- On-demand by Finance / MD via `/api/agents/run`

## Actions

| Action          | Description                                          | Autonomy |
|-----------------|------------------------------------------------------|----------|
| forecast        | Run 13-week model and persist to CashForecast table | A1       |
| report          | Read persisted forecast for a given entity+period    | A1       |
| update_actuals  | Reserved — record actual cash flows for accuracy KPI| Manual   |

## Model Parameters

| Bucket    | Weekly Collection Probability |
|-----------|-------------------------------|
| current   | 35 %                          |
| d1_30     | 20 %                          |
| d31_60    | 10 %                          |
| d61_90    | 5 %                           |
| over_90   | 2 %                           |

Promise reliability multiplier from latest `promise_reliability` KPI (default 0.70).

## Constraints

- All arithmetic in Python `Decimal` — never `float` for monetary values
- AP outflow spread is linear over 4 weeks — no guessing payment timing
- Confidence percentage decreases linearly over the 13-week horizon
- Forecast is advisory — never triggers payments automatically

## Related

- [[f1_collections]] — promise_reliability KPI feeds this model
- [[d1_ask_business]] — exposes forecast data via `read_kpi`
