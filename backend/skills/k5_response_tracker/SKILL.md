# Response Tracker — K5
**Version:** 1.0.0  
**Autonomy:** A2  
**Go-live:** 2026-09-11  
**Primary KPI:** `response_rate_pct`  
**KPI Target:** ≥ 20%

## Purpose
Captures and classifies inbound responses to marketing broadcasts, including WhatsApp replies, email click-throughs, and form submissions. Categorises responses as hot leads, enquiries, or opt-outs, and routes them to appropriate follow-up queues. Ensures at least 20% of broadcast recipients generate a trackable response.

## Trigger
Triggered on inbound webhook events from the WhatsApp gateway or email platform following a K3 broadcast. Also runs hourly sweeps to reconcile broadcast delivery events with response records.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| receive_inbound_response | Capture raw inbound message/click from gateway webhook | A2 |
| classify_response | Tag response as HOT_LEAD, ENQUIRY, OPT_OUT, or UNCLASSIFIED | A2 |
| update_contact_record | Append response event to the customer/lead contact record | A2 |
| route_hot_lead | Send HOT_LEAD to sales follow-up queue for ISM/sales team | A2 |
| process_opt_out | Immediately update marketing consent to opted-out | A2 |
| report_response_rate | Calculate and log response rate against campaign broadcast count | A2 |

## Constraints
- Opt-out requests must be processed immediately and never queued for later action.
- Never misclassify an opt-out as a lead; opt-out takes precedence over all other signals.
- Response rate below 20% at campaign midpoint triggers an alert to Marketing Manager.
- UNCLASSIFIED responses must be reviewed within 48 hours; they cannot remain unclassified indefinitely.
- All inbound messages are logged verbatim for compliance; no content is deleted.
