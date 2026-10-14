# AMC Engine — V2
**Version:** 1.0.0  
**Autonomy:** A2  
**Go-live:** 2026-09-11  
**Primary KPI:** `amc_renewal_rate_pct`  
**KPI Target:** ≥ 90%

## Purpose
Manages Annual Maintenance Contract (AMC) renewal lifecycle for Yexis Electronics service customers. Proactively identifies contracts approaching expiry, generates renewal notices, and tracks customer responses to ensure the AMC renewal rate stays at or above 90%. Escalates unresponsive customers to the Service Manager for personal follow-up.

## Trigger
Runs daily to check contracts expiring within the next 30 days. Also triggered on demand by the Service Manager or on receipt of a renewal-request webhook.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| fetch_expiring_contracts | Query AMC table for contracts expiring within N days | A2 |
| generate_renewal_notice | Draft and dispatch renewal notice via WhatsApp/email | A2 |
| log_customer_response | Record accept/reject/no-response against contract | A2 |
| process_renewal_payment | Initiate payment link generation for accepted renewals | A2 (human gate for amount > ₹50,000) |
| escalate_unresponsive | Flag contracts with no response after 14 days to Service Manager | A2 (human gate) |
| update_amc_status | Set contract status to RENEWED, LAPSED, or PENDING | A2 |

## Constraints
- Never auto-renew an AMC without customer confirmation.
- Never dispatch a payment link without validating contract terms and pricing.
- Escalate to Service Manager before marking any contract LAPSED.
- Do not send more than 3 renewal reminders per contract per cycle.
- All communications must include contract number, expiry date, and renewal amount.
