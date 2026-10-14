# Policy: P3 Expediting

## Decision rules (implemented in app/services/expediting.py)
| Rule | Value |
|---|---|
| verdict | SLIPPED (days_late > 0) → UNCONFIRMED (confirmed=False) → DUE_SOON (≥ T-3) → ON_TRACK |
| days_late | today − committed_date |
| confirmation window | T-3 days before committed date |
| escalation | buyer_call (>3d late) → whatsapp_chase (late or unconfirmed) → email_confirmation (T-3 window) → none |
| impact | HIGH (late + milestone/customer order) → WATCH (stock-out risk) → NONE |
| mitigation status | proposed (partial_shipment, alternate_vendor) / pending_approval (everything else, regardless of claimed cost) |
| OTIF | on_time / total rounded to 4dp; no data → None (never a fabricated 100%) |

## Constants
| Parameter | Value | Notes |
|---|---|---|
| CONFIRM_WINDOW_DAYS | 3 | the spec's T-3 confirmation request |
| CHASE_AFTER_DAYS | 3 | silence past due deepens the ladder |
| NO_COST_MITIGATIONS | partial_shipment, alternate_vendor | auto-proposed; status stays "proposed" |

## Cadence
- Daily sweep of open POs (morning, before the buyer's vendor-call
  window); immediate re-run on a reported delay via the mailbox/WhatsApp
  intake lanes.

## Escalation
- HIGH impact slips: proactive WhatsApp update to requester/PM the same
  day, plus a mitigation proposal for the buyer.
- Project-critical slippage is flagged for the buyer to escalate to MD —
  the agent never contacts the MD itself.
- Vendors with OTIF below the review threshold surface on the monthly
  vendor-review pack (fed from vendor_scorecards).
