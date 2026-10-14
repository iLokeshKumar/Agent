# Skill: F2 Scheme Claims

## Identity
Samsung scheme-claim engine (Yexis-unique): compute earned claims from
serialised sales against verified scheme windows, then drive computed →
packed → submitted → settled/disputed. Suite: Finance. Runtime: hybrid
(compute headless; pack assembly + submission supervised in Cowork).

## Procedure
1. Load the scheme from the register — only `verified` schemes may
   compute. A parsed circular is a candidate until a human verifies it.
2. Join `serial_registry` (status=sold, sold_invoice_ref) to invoice
   base values inside the scheme window.
3. Compute the earned claim with the deterministic engine
   (`app/services/scheme_claims.py`) — slabs, exclusions, double-claim
   guard, already-claimed offset. Never estimate amounts in prompts.
4. Render the claim pack: exclusions first, earned lines with invoice
   refs, slab ladder evidence.
5. Commercial head reviews in Cowork → `record_claim` with the claimed
   amount (overclaim flagged against computation).
6. On vendor credit note → `record_settlement` (full / partial /
   disputed), variance reason recorded.

## Tool bindings
| Tool | Use |
|---|---|
| serial_registry reads | sold serials + sale dates |
| scheme_master (Local-DB MCP) | verified scheme windows/slabs |
| doc_index reads | circular evidence for the pack |
| claim templates | claim pack (Word/Excel) |

## Autonomy & HITL
- compute: A2 (facts only, nothing submitted).
- record_claim / record_settlement: A1 — explicit human action,
  ledgered.

## Never
- Compute against an unverified (candidate) scheme.
- Claim a serial already on a claim line — the engine refuses; never
  work around it.
- Submit without the human's recorded claim amount.
