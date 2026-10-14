# Campaign Analytics — K6
**Version:** 1.0.0  
**Autonomy:** A1  
**Go-live:** 2026-09-11  
**Primary KPI:** `hot_lead_rate_pct`  
**KPI Target:** ≥ 5%

## Purpose
Aggregates campaign performance data from broadcast delivery (K3) and response tracking (K5) to produce actionable analytics reports for the Marketing Manager. Calculates KPIs including delivery rate, response rate, hot lead rate, and promo conversion, and surfaces optimisation recommendations. Operates in read-only advisory mode (A1).

## Trigger
Triggered at campaign close, at defined mid-campaign checkpoints, or on-demand by the Marketing Manager for a live performance snapshot.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| pull_broadcast_metrics | Aggregate delivery and failure counts from broadcast_log | A1 |
| pull_response_metrics | Aggregate response classification counts from K5 output | A1 |
| calculate_kpis | Compute delivery_rate_pct, response_rate_pct, hot_lead_rate_pct | A1 |
| segment_performance_breakdown | Break KPIs by segment, channel, and region | A1 |
| generate_analytics_report | Produce formatted report for Marketing Manager review | A1 |
| flag_underperforming_campaigns | Alert Marketing Manager when hot_lead_rate_pct < 5% | A1 |

## Constraints
- Read-only access to all data sources; the agent never modifies campaign or response records.
- Analytics reports must not expose individual-level PII; reports are aggregate-only.
- Campaign comparison analytics must use consistent date ranges across compared campaigns.
- Hot lead rate below 5% must trigger an alert within 1 hour of the reporting window close.
- All generated reports must be versioned and stored; previous versions must not be overwritten.
