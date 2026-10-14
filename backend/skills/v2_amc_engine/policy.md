# V2 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| Renewal amount ≤ ₹50,000 | Auto-process payment link on customer confirmation |
| Renewal amount > ₹50,000 | Service Manager approval required before payment link |
| No customer response after 14 days | Service Manager manual follow-up gate |
| Contract lapse requested by customer | Service Manager sign-off before LAPSED status |
| Bulk renewal run > 50 contracts | Service Manager review of notice batch before dispatch |

## Constraints
1. The agent must never auto-renew or charge a customer without explicit confirmation.
2. Reminder frequency is capped at 3 per contract per renewal cycle to prevent spam.
3. AMC renewal rate KPI below 90% triggers a weekly review escalation to Service Manager.
4. Contract terms (pricing, coverage) must be sourced from the master AMC catalogue only.
5. All renewal transactions must be audit-logged with customer ID, contract ID, and timestamp.

## Data
- Source: amc_contracts table, customer_master, amc_catalogue, payment_gateway
- Output: amc_renewal_events table, customer_communications_log, payment_links queue
