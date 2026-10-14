# Dispatch Tracker — C2
**Version:** 1.0.0  
**Autonomy:** A2  
**Go-live:** 2026-09-11  
**Primary KPI:** `dispatch_to_pod_days`  
**KPI Target:** ≤ 3 days

## Purpose
Tracks outbound shipments from dispatch through to proof-of-delivery (POD), ensuring every consignment achieves POD confirmation within 3 days. Automatically chases logistics partners for delayed shipments and flags unresolved delays to the SCM Manager for intervention.

## Trigger
Runs continuously on dispatch events. Also executes a daily sweep to identify shipments that have exceeded the 3-day dispatch-to-POD target without confirmation.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| register_dispatch | Log outbound shipment with LR number, consignee, and dispatch date | A2 |
| poll_pod_status | Query logistics partner API for delivery confirmation | A2 |
| send_chase_alert | Send automated chase to transporter for shipments > 2 days without POD | A2 |
| record_pod | Capture POD reference and close the shipment record | A2 |
| escalate_delay | Route shipments beyond 3 days without POD to SCM Manager | A2 (human gate) |

## Constraints
- Never mark a shipment as delivered without a valid POD reference number.
- Never close a disputed delivery without SCM Manager resolution.
- Chase alerts must not exceed 2 per shipment before human escalation.
- Dispatch records are immutable once created; amendments require a correction entry.
- All dispatch-to-POD durations exceeding 3 days must be reported in the daily exceptions log.
