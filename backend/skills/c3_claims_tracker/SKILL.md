# SCM Claims Tracker — C3
**Version:** 1.0.0  
**Autonomy:** A2  
**Go-live:** 2026-09-11  
**Primary KPI:** `scm_claim_resolution_days`  
**KPI Target:** ≤ 30 days

## Purpose
Manages the end-to-end lifecycle of SCM claims — including short-delivery, damage-in-transit, and vendor credit claims — ensuring each claim reaches resolution within 30 days. Tracks claim submissions, chases vendors and transporters for responses, and escalates stalled claims to the SCM Manager.

## Trigger
Triggered when a new claim is filed (via API or WhatsApp), and runs a daily sweep to chase open claims approaching or breaching the 30-day resolution target.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| register_claim | Create a new claim record with type, value, counterparty, and evidence refs | A2 |
| dispatch_claim_notice | Send formal claim notice to vendor or transporter | A2 |
| log_counterparty_response | Record vendor/transporter response or credit note | A2 |
| update_claim_status | Transition claim to ACCEPTED, REJECTED, PARTIAL, or ESCALATED | A2 |
| send_chase_reminder | Automated follow-up for claims with no response after 10 days | A2 |
| escalate_stalled_claim | Route claims > 25 days without resolution to SCM Manager | A2 (human gate) |

## Constraints
- Never close a claim without documented counterparty response or SCM Manager override.
- Never accept a credit note for an amount differing from the claimed value without SCM Manager approval.
- Chase reminders must not exceed 3 per claim before human escalation.
- Claim evidence (photos, delivery docs) must be attached at registration; claims without evidence are rejected.
- Resolution KPI breaches (> 30 days) must be escalated immediately with ageing summary.
