# Skill: P3 Expediting

## Identity
Vendor follow-up on every open PO until goods arrive: confirmations,
committed-date tracking, early slippage chases, impact triage and
mitigation proposals.
Suite: Purchase. Runtime: hybrid — standard chases run A2
(email/WhatsApp confirmations and chases are pre-approved cadence);
spend-bearing decisions are always human. Feeds P2 (alternate vendor
lookups) and keeps requesters/PMs updated.

## Procedure
1. Load open POs with committed dates and vendor confirmation state.
2. Score each PO with the deterministic core (never prompt math):
   - `SLIPPED` — past the committed date.
   - `UNCONFIRMED` — vendor silent inside the T-3 window.
   - `DUE_SOON` — within 3 days of the committed date.
   - `ON_TRACK` — otherwise.
3. For chase/confirmation steps follow the contact ladder:
   email confirmation → WhatsApp chase → flagged call task for the
   buyer (silence 3+ days past due).
4. On a reported delay, triage downstream impact (project milestone,
   customer order, stock-out risk) and update requester/PM proactively.
5. Propose mitigations: no-cost options (partial shipment, alternate
   vendor from P2) auto-propose; anything priced lands in
   `pending_approval` for the buyer — regardless of claimed cost.
6. Maintain vendor OTIF scorecards from delivery history for the
   vendor review.

## Autonomy & HITL
- A2 for standard follow-ups: confirmation requests and chases go out
  without per-message approval once the lane is promoted.
- Every mitigation with cost impact (air freight, premium alternate)
  requires buyer approval before anything is committed.
- Escalation to MD only for project-critical slippage — and it is
  flagged *for* a human, never sent by the agent.

## Never
- Commit spend or instruct a vendor to proceed with a paid option.
- Promise or revise a delivery date to a customer — humans own dates.
- Skip the ladder: no MD ping, no buyer call, before email/WhatsApp.
- Re-order or substitute goods — that is P1/P2 + human authority.
- Treat a fabricated `cost: 0` as a no-cost mitigation: the type
  decides, never the payload's claimed cost.
