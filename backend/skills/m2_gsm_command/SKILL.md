# GSM Command — M2
**Version:** 1.0.0  
**Autonomy:** A1  
**Go-live:** 2026-09-11  
**Primary KPI:** — (read-only)  
**KPI Target:** —

## Purpose
Provides the General Sales Manager (GSM) with a consolidated, read-only command view across all sales channels, business units, and geographies. Aggregates data from internal sales (M1), external dealer network, service revenue (V-suite), and marketing pipeline (K-suite) to support strategic sales leadership decisions. No KPI targets are enforced autonomously; this is a pure insight and reporting agent.

## Trigger
Triggered daily at 07:30 IST to deliver the GSM morning command briefing. Also triggered on-demand by the GSM or on escalation from M1 when DSO exceeds 60 days.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| aggregate_sales_summary | Roll up total bookings, billings, and collections across all channels | A1 |
| pull_ism_briefing | Incorporate latest M1 ISM briefing into GSM view | A1 |
| dealer_network_snapshot | Summarise dealer sell-out, scheme participation, and outstanding | A1 |
| service_revenue_view | Pull service and AMC revenue contribution from V-suite | A1 |
| cross_suite_kpi_dashboard | Consolidate KPIs from S, C, K, V, and M1 suites | A1 |
| generate_gsm_briefing | Produce comprehensive daily GSM command dashboard | A1 |

## Constraints
- Strictly read-only across all data sources; the agent never modifies any record in any suite.
- GSM briefing must not include any un-aggregated individual employee or dealer PII.
- Data freshness requirement: all source data must be from the current day; stale data triggers a freshness warning.
- The agent does not make operational decisions; all recommendations are advisory for the GSM.
- GSM briefing access is restricted to GSM-level and above; access requests from lower roles are rejected.
