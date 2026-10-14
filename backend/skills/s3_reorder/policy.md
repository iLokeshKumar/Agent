# Policy: S3 Reorder

## Formulas (implemented in app/services/reorder.py)
| Quantity | Formula |
|---|---|
| avg_weekly_demand | mean(weekly_units) |
| reorder_point | ceil(avg_weekly_demand × lead_time_weeks + safety_stock) |
| needs_reorder | on_hand ≤ reorder_point |
| order_quantity | ceil(max(ROP, avg_weekly × target_cover_weeks) − on_hand), min applied if 0 < qty < min_order_qty |

## Defaults
| Parameter | Default | Notes |
|---|---|---|
| target_cover_weeks | 4 | raise for slow-movers on project SKUs |
| min_order_qty | 0 (none) | per-vendor MOQ from vendor_scorecard |
| safety_stock | 0 | set per SKU from service-level targets |

## Cadence
- Weekly run (Monday 06:00 local) via the S3 lane; ad-hoc runs allowed
  after physical count corrections.

## Escalation
- EOL SKUs flagged by S2 route their proposals to clearance instead of
  replenishment — S3 must not reorder an EOL SKU.
- Repeated proposals (same SKU 3 weeks running) get a human review
  card — likely a data problem, not a demand problem.
