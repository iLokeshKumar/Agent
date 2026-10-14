# K3 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| Campaign approved + audience confirmed by K2 | Auto-broadcast at scheduled time |
| Delivery rate drops below 90% (rolling 500 msgs) | Auto-pause; Marketing Manager must resume |
| Template not pre-approved in WhatsApp BSP | VALIDATION_ERROR; template must be registered |
| Broadcast > 50,000 messages | Marketing Manager confirmation required before start |
| Broadcast attempted outside 09:00–20:00 IST | Auto-defer to next permitted window; Marketing Manager notified |

## Constraints
1. Only pre-registered WhatsApp Business Solution Provider (BSP) templates may be used.
2. Delivery rate KPI must remain ≥ 95% on a per-campaign basis; sustained failure triggers a postmortem.
3. The agent must honour TRAI DND registry rules; DND-registered numbers are excluded automatically.
4. Rate limits: max 1,000 messages per minute; max 100,000 per 24-hour window per WhatsApp account.
5. All send events are persisted in the broadcast_log table for compliance and analytics use by K6.

## Data
- Source: campaign_audiences, campaign_templates, campaigns table
- Output: broadcast_log, delivery_status_events, campaign_kpi_snapshots
