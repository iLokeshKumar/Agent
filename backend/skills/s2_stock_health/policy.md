# Policy: S2 Stock Health

## Decision rule (implemented in app/services/stock_health.py)
Order matters — first match wins:
| # | Condition | action_reco |
|---|---|---|
| 1 | EOL and on_hand > 0 | clearance_push |
| 2 | stale (61_90 + over_90) ≥ half of on_hand | ageing_review |
| 3 | weeks_cover < 2.0 | reorder_check |
| 4 | otherwise | healthy |

## Formulas
| Quantity | Formula |
|---|---|
| avg_weekly_demand | mean(weekly_units) |
| weeks_cover | on_hand / avg_weekly_demand (None when demand = 0) |
| ageing buckets | 0–30 / 31–60 / 61–90 / 90+ days since receipt |

## Thresholds
| Parameter | Value | Notes |
|---|---|---|
| LOW_COVER_WEEKS | 2.0 | below this, hand to S3 |
| EOL source | vendor EOL calendar, set per SKU in the lane payload | maintained by ops |

## Cadence
- Weekly (Monday 06:30 local, before S3's 06:00 reads of yesterday's
  sku_health — ops may reorder the two lanes per site).

## Escalation
- Any SKU with over_90 units is listed on the brief's front page even
  when otherwise healthy — stale serials are capital at risk.
- Discrepancy between serial_registry count and physical count → open
  an S1 correction flow, never edit sku_health directly.
