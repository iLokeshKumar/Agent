# M2 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| Daily GSM briefing generation | Auto-generate at 07:30 IST; no gate |
| Cross-suite KPI report including financial data | GSM must confirm scope of report |
| M1 DSO escalation > 60 days | Auto-include in GSM briefing; GSM decides intervention |
| GSM briefing shared externally or with Board | Business Head approval required |
| Access by non-GSM role | Access denied; logged as unauthorised access attempt |

## Constraints
1. Read-only access to all suites (S, C, K, V, M1, F); no writes or updates are permitted.
2. Aggregation logic is standardised; per-suite KPI definitions must match the suite agent's own definitions.
3. The GSM briefing is a synthesis document; it never exposes raw transactional data.
4. Briefings are retained for 180 days and are available for Board-level review on request.
5. Any data source unavailability (e.g., M1 not yet run) is surfaced as a data gap warning, not a failure.

## Data
- Source: ism_briefings (M1), dealer_network_data, service_revenue (V-suite), campaign_kpi_snapshots (K-suite), scm_kpi_snapshots (C-suite), inventory_kpi_snapshots (S-suite) — all read-only
- Output: gsm_briefings table, cross_suite_kpi_dashboard, gsm_alert_queue
