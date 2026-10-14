# K6 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| Standard campaign analytics report | Auto-generate; Marketing Manager receives notification |
| hot_lead_rate_pct < 5% | Alert to Marketing Manager within 1 hour; no auto-action |
| Report containing cross-campaign comparison | Marketing Manager must confirm scope before generation |
| PII-level drill-down requested | Access denied; refer to Marketing Manager with data-privacy justification |
| Campaign benchmark data shared externally | Business Head approval required |

## Constraints
1. The agent has read-only access; it cannot modify any campaign, response, or contact record.
2. All reports are aggregate-level; no individual contact identifiers appear in report output.
3. KPI calculation methodology must be documented in the report header and consistent across campaigns.
4. Reports are immutable once generated; corrections require a new report version with change note.
5. Analytics data latency must not exceed 4 hours from event occurrence to report availability.

## Data
- Source: broadcast_log, campaign_responses, campaign_audiences, promo_kpi_snapshots (read-only)
- Output: campaign_analytics_reports table, kpi_alert_queue, marketing_dashboard_snapshots
