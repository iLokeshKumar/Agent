# K5 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| OPT_OUT response received | Immediate processing; no gate; confirmation sent to contact |
| HOT_LEAD classification | Auto-route to sales queue; no gate |
| UNCLASSIFIED response after 48 hours | Marketing Manager review required for reclassification |
| Response rate < 20% at campaign 50% completion | Marketing Manager intervention alert |
| Bulk re-classification of > 100 responses | Marketing Manager approval required |

## Constraints
1. Opt-out status updates are irreversible via this agent; re-opt-in requires explicit customer action.
2. Response classification model must be validated by Marketing Manager before each campaign type.
3. HOT_LEAD routing must complete within 15 minutes of response receipt to maintain lead freshness.
4. Response data feeds directly into K6 Campaign Analytics; data quality issues block analytics runs.
5. All webhook events are stored raw in the inbound_responses archive before any processing.

## Data
- Source: whatsapp_gateway_webhooks, email_platform_webhooks, campaign_audiences, broadcast_log
- Output: campaign_responses table, marketing_consent_register (opt-outs), sales_lead_queue, response_kpi_snapshots
